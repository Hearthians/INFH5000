"""Durable generation/review loop shared by CLI, batch and browser workers."""

import fcntl
import os
import time
import uuid

from .comfy import Comfy, SubmissionRejected
from .proposals import FRAME, step_instruction
from .regions import composite, crop_input, edit_polygons, valid_polygon
from .storage import read_json, user_images, write_json
from .vision import CodexVision, check_intent, review_with_retry


def failures(report):
    return {key: item for key, item in report["checks"].items() if item["verdict"] != "pass"}


def score(report):
    checks = report["checks"]
    semantic = [v for k, v in checks.items() if k not in {"marks", "quality"}]
    return (sum(v["verdict"] == "pass" for v in semantic),
            sum(v["verdict"] == "pass" for v in checks.values()),
            -sum(v["verdict"] == "fail" for v in checks.values()))


def accept_continuation(state, answer):
    if state["status"] == "needs_clarification":
        if not answer or not answer.strip():
            raise ValueError("请先回答澄清问题")
        state["answers"].append({"question": state["question"], "answer": answer.strip()})
        state["question"] = None
    elif state["status"] != "error" or answer:
        raise ValueError("任务当前不需要此操作")
    state.update(status="queued", stage="queued", error=None)


def queue_continuation(directory, answer=None):
    # Persist the answer before acknowledging the HTTP request. A queued job also
    # survives a server restart while both web workers are busy.
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        file = directory / "state.json"
        state = read_json(file)
        accept_continuation(state, answer)
        write_json(file, state)


def new_attempt(state):
    number = len(state["attempts"]) + 1
    base = "inputs/source.png" if state["has_source"] else "inputs/canvas.png"
    mode, reason, polygons, parent = "original", "Initial edit from user input.", [], None
    feedback = []
    if number > 1:
        best = state["attempts"][state["best"]-1]
        last = state["attempts"][-1]
        for idx in dict.fromkeys((last["number"], best["number"])):
            previous = state["attempts"][idx-1]
            feedback.append({"attempt": idx, "failed_checks": failures(previous["review"])})
        reason = f"Redo from original after attempt {last['number']}. Best reviewed attempt is {best['number']}."
        bad = failures(best["review"])
        cleaned = any(a["mode"] == "cleanup" and a["parent_attempt"] == best["number"]
                      for a in state["attempts"])
        if (set(bad) == {"marks"} and bad["marks"]["verdict"] == "fail"
                and best["mode"] != "cleanup" and not cleaned):
            mode, base, parent = "cleanup", best["candidate"], best["number"]
            ink = best["review"]["ink_regions"]
            polygons = ink if all(valid_polygon(p) for p in ink) else []
            reason = f"Only annotation ink failed on attempt {parent}. Clean that candidate."
        elif state["has_source"] and len(state["intent"].get("approved_steps", [])) <= 1:
            polygons = edit_polygons(state["intent"])
            if polygons:
                mode = "crop"
                reason += " The target footprint is reliable; regenerate it with surrounding context."
    return dict(number=number, mode=mode, base=base, parent_attempt=parent, input=base,
                reason=reason, feedback=feedback, polygons=polygons, crop=None,
                seed=41+number, steps=40, prompt=None, prompt_id=None, submitted_at=None,
                candidate=None, review=None, regressed=False)


class Runner:
    def __init__(self, directory, vision=None, generator=None):
        self.directory = directory
        self.vision = vision if vision is not None else CodexVision()
        self.generator = generator if generator is not None else Comfy()

    def save(self, stage):
        self.state["stage"] = stage
        self.state["elapsed_s"] = round(self.previous_elapsed + time.monotonic() - self.started, 2)
        write_json(self.directory / "state.json", self.state)

    def run(self, *, answer=None, resume_error=False):
        with (self.directory / ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return read_json(self.directory / "state.json")
            self.state = state = read_json(self.directory / "state.json")
            status = state["status"]
            if status in {"passed", "exhausted"} or (status == "error" and not resume_error):
                return state
            if status == "needs_clarification" and not (answer and answer.strip()):
                return state
            if status in {"error", "needs_clarification"} or answer:
                accept_continuation(state, answer)
            self.started, self.previous_elapsed = time.monotonic(), state["elapsed_s"]
            state.update(status="running", error=None)
            try:
                self.execute()
            except Exception as exc:
                state.update(status="error", error=str(exc) or type(exc).__name__)
                state.setdefault("errors", []).append({"time": time.time(), "stage": state["stage"],
                                                       "attempt": len(state["attempts"]), "detail": state["error"]})
                self.save(state["stage"])
            return state

    def execute(self):
        state, folder = self.state, self.directory
        images = user_images(folder, state)
        if state["intent"] is None:
            self.save("planning")
            context = dict(note=state["note"], clarifications=state["answers"],
                           strokes=read_json(folder / "inputs/strokes.json"), source_available=state["has_source"])
            if state.get("proposal"):
                context["approved_steps"] = state["proposal"]["steps"]
                context["coordinate_system"] = FRAME
            intent = check_intent(self.vision.plan(folder / f"plan-{len(state['answers'])+1}", images, context))
            if intent["status"] == "clarify":
                state.update(status="needs_clarification", question=intent["question"])
                self.save("clarification")
                return
            if state.get("proposal"):
                intent = {**intent, "approved_steps": state["proposal"]["steps"]}
            state["intent"] = intent
            self.save("planned")
        while True:
            attempts = state["attempts"]
            if not attempts or attempts[-1]["review"] is not None:
                if attempts and not failures(attempts[-1]["review"]):
                    state["status"] = "passed"
                    break
                if len(attempts) >= state["max_tries"]:
                    state["status"] = "exhausted"
                    break
                attempts.append(new_attempt(state))
                self.save("preparing")
            attempt = attempts[-1]
            sub = folder / f"attempt-{attempt['number']}"
            sub.mkdir(exist_ok=True)
            if not attempt["prompt"]:
                self.prepare(attempt, sub, images)
            if not attempt["candidate"]:
                self.generate(attempt, sub)
            self.save("reviewing")
            candidate = ("actual full-frame candidate to inspect", folder / attempt["candidate"])
            report = review_with_retry(self.vision, sub, images + [candidate], state["intent"])
            previous = attempts[state["best"]-1] if state["best"] is not None else None
            attempt["review"] = report
            if previous is None or score(report) > score(previous["review"]):
                state["best"] = attempt["number"]
            elif score(report) < score(previous["review"]):
                attempt["regressed"] = True
            state["remaining"] = failures(attempts[state["best"]-1]["review"])
            self.save("reviewed")
        self.save("finished")

    def prepare(self, attempt, sub, images):
        state, folder = self.state, self.directory
        if attempt["mode"] == "crop":
            image = sub / "crop.png"
            attempt["crop"] = crop_input(folder / attempt["base"], attempt["polygons"], image)
            attempt["input"] = str(image.relative_to(folder))
        prompt = state["intent"]["prompt"]
        if attempt["number"] > 1:
            self.save("revising")
            last = state["attempts"][-2]
            attachments = images + [("most recent full candidate, before this retry", folder / last["candidate"])]
            attachments.append(("actual image Qwen will receive on this attempt", folder / attempt["input"]))
            context = dict(frozen_intent=state["intent"], feedback=attempt["feedback"],
                           last_prompt=last["prompt"], mode=attempt["mode"], input=attempt["input"],
                           reason=attempt["reason"], crop_in_source_pixels=attempt["crop"])
            prompt = self.vision.revise(sub / "revision", attachments, context)
        if attempt["mode"] == "original" and state["intent"].get("approved_steps"):
            steps = state["intent"]["approved_steps"]
            prompt += "\n" + FRAME + "\n" + "\n".join(
                f"{index}. {step_instruction(step)}" for index, step in enumerate(steps, 1))
        if attempt["mode"] == "cleanup":
            prompt += " Remove only instruction ink and lettering. Preserve the completed edit and all scene details."
        else:
            prompt += " Deliver a clean image, free of annotation strokes, arrows, circles and instruction lettering."
        attempt["prompt"] = prompt
        (sub / "prompt.txt").write_text(prompt, encoding="utf-8")
        self.save("prepared")

    def generate(self, attempt, sub):
        state, folder = self.state, self.directory
        raw = sub / "generated.png"
        if not raw.exists():
            if attempt["submitted_at"] is None:
                self.save("uploading")
                prefix = f"agent-qwen/{state['id']}/{sub.name}"
                graph = self.generator.workflow(folder / attempt["input"], attempt["prompt"], attempt["seed"], prefix)
                write_json(sub / "workflow.json", graph)
                attempt["backend"] = getattr(self.generator, "url", None)
                attempt["prompt_id"] = str(uuid.uuid4())
                attempt["submitted_at"] = time.time()
                state["generation_count"] += 1
                self.save("submitting")
                try:
                    attempt["prompt_id"] = self.generator.submit(graph, attempt["prompt_id"])
                except SubmissionRejected as exc:
                    attempt.setdefault("rejected_submissions", []).append(
                        {"prompt_id": attempt["prompt_id"], "error": str(exc)})
                    attempt.update(prompt_id=None, submitted_at=None)
                    state["generation_count"] -= 1
                    raise
            self.save("generating")
            generator = self.generator
            if isinstance(generator, Comfy) and attempt.get("backend") and generator.url != attempt["backend"]:
                generator = Comfy(attempt["backend"])
            generator.collect(attempt["prompt_id"], raw)
        candidate = sub / "candidate.png"
        temp = sub / "candidate.tmp"
        if attempt["polygons"]:
            composite(folder / attempt["base"], raw, temp, attempt["polygons"], attempt["crop"])
            temp.replace(candidate)
        elif not candidate.exists():
            os.link(raw, candidate)
        attempt["candidate"] = str(candidate.relative_to(folder))
        self.save("generated")
