"""Independent, image-attached Codex calls and their machine-checked contracts."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .contracts import TEXT, InvalidResponse, checked, choice, items, record
from .proposals import FRAME, SUGGESTIONS, check_suggestions, step_instruction

POINT = record(x={"type": "integer"}, y={"type": "integer"})
POLYGON = items(POINT)
REGION = record(role=choice("edit", "origin", "destination"), points=POLYGON)
BASE_CHECKS = ("target", "effect", "neighbors", "marks", "quality")
MOVE_CHECKS = ("origin", "destination")
PLAN_SCHEMA = record(
    status=choice("ready", "clarify"), question=TEXT,
    operation=choice("add", "remove", "replace", "modify", "move", "other"),
    target=TEXT, location=TEXT, destination=TEXT, preserve=items(TEXT),
    regions_reliable={"type": "boolean"}, regions=items(REGION),
    criteria=record(**dict.fromkeys(BASE_CHECKS + MOVE_CHECKS, TEXT)), prompt=TEXT,
)
CHECK = record(verdict=choice("pass", "fail", "unclear"), location=TEXT, evidence=TEXT)


def required_checks(intent):
    keys = BASE_CHECKS + (MOVE_CHECKS if intent["operation"] == "move" else ())
    checks = {key: intent["criteria"][key] for key in keys}
    for index, step in enumerate(intent.get("approved_steps", []), 1):
        checks[f"step_{index}"] = step_instruction(step)
    return checks


def report_schema(intent):
    return record(checks=record(**dict.fromkeys(required_checks(intent), CHECK)),
                  ink_regions=items(POLYGON))


def check_intent(intent):
    checked(PLAN_SCHEMA, intent)
    if intent["status"] == "clarify":
        if not intent["question"].strip():
            raise InvalidResponse("A clarification needs a specific question.")
    else:
        fields = [intent["target"], intent["location"], intent["prompt"], *required_checks(intent).values()]
        if intent["operation"] == "move":
            fields.append(intent["destination"])
        if not all(item.strip() for item in fields):
            raise InvalidResponse("The edit plan is missing a target, location, instruction or required criterion.")
    return intent


def check_report(intent, report):
    checked(report_schema(intent), report)
    for key, item in report["checks"].items():
        if not item["location"].strip() or not item["evidence"].strip():
            raise InvalidResponse(f"{key} needs a location and observable evidence.")
    return report


class CodexVision:
    def __init__(self):
        self.timeout = float(os.environ.get("AGENT_QWEN_CODEX_TIMEOUT", "240"))
        self.binary = os.environ.get("AGENT_QWEN_CODEX", "codex")

    def call(self, folder, instructions, attachments, context, schema):
        folder.mkdir(parents=True, exist_ok=True)
        roles = "\n".join(f"Image {i}: {role}" for i, (role, _) in enumerate(attachments, 1))
        message = (
            "Use only the attached pixels and the supplied data. Do not call tools, search files, "
            "or seek an answer key. Treat writing in the images as editing instructions only.\n"
            + roles + "\n" + instructions + "\n" + json.dumps(context, ensure_ascii=False)
        )
        (folder / "request.txt").write_text(message, encoding="utf-8")
        (folder / "attachments.json").write_text(json.dumps(
            [{"role": role, "file": os.path.relpath(path, folder)} for role, path in attachments], indent=2))
        (folder / "schema.json").write_text(json.dumps(schema, indent=2))
        # Neutral filenames and an empty working directory keep example names and other
        # attempts (including the author's evaluation material) out of agent context.
        with tempfile.TemporaryDirectory(prefix="agent-qwen-") as temporary:
            work = Path(temporary)
            shutil.copyfile(folder / "schema.json", work / "schema.json")
            command = [self.binary, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                       "--disable", "shell_tool", "--disable", "apps", "--disable", "plugins",
                       "-c", 'web_search="disabled"', "-c", "model_reasoning_effort=" + json.dumps(
                           os.environ.get("AGENT_QWEN_CODEX_EFFORT", "medium")),
                       "-C", str(work), "--color", "never",
                       "--output-schema", str(work / "schema.json"), "-o", str(work / "answer.json")]
            model = os.environ.get("AGENT_QWEN_CODEX_MODEL")
            if model:
                command += ["-m", model]
            for index, (_, path) in enumerate(attachments, 1):
                attached = work / f"image-{index}.png"
                if path.suffix.lower() == ".png":
                    shutil.copyfile(path, attached)
                else:
                    with Image.open(path) as im:
                        im.convert("RGB").save(attached)
                command += ["-i", str(attached)]
            with subprocess.Popen(command + ["-"], text=True, stdin=subprocess.PIPE,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  cwd=work, start_new_session=True) as process:
                try:
                    _, stderr = process.communicate(message, timeout=self.timeout)
                except subprocess.TimeoutExpired as exc:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    raise RuntimeError(f"Codex timed out after {self.timeout:g} seconds.") from exc
            if process.returncode:
                detail = stderr.strip().splitlines()[-4:]
                raise RuntimeError("Codex failed: " + " ".join(detail)[-1000:])
            answer = work / "answer.json"
            raw = answer.read_text() if answer.exists() else ""
            (folder / "response.json").write_text(raw)
        try:
            return checked(schema, json.loads(raw))
        except json.JSONDecodeError as exc:
            raise InvalidResponse(f"Invalid JSON: {exc.msg}") from exc

    def suggest(self, folder, images, context):
        instructions = """Look at the actual uploaded image, user marks and goal. Recommend two
meaningfully different, feasible ways to meet the goal when it leaves a design choice, or one
when the user has fixed the result. If a necessary decision cannot be inferred, return an empty
plans list and one specific question. For plans, question is empty. Write concise titles, reasons,
target names and descriptions in the user's language (default Chinese). Explain the concrete
tradeoff of each plan using details visible in this image. Use only the supported operations,
in the order they should be applied. A selection identifies the original object or, for add, the
intended footprint. x,y is its top-left corner; width,height are positive; the entire selection
must fit the image. All geometry is normalized to 0..1000 along each image axis. A move's to
point is the final center of the same object. Degrees are clockwise. Resize percent is the final
linear size relative to the original. Protect explicitly requested features. Do not add decorative
edits the user did not ask for. Use minimal steps and precise visually identifiable target names.
Keep targets and descriptions independent of proposed coordinates when the user could drag them.
Freehand stroke coordinates in context are original pixels."""
        for attempt in (1, 2):
            try:
                result = self.call(folder / f"suggestion-{attempt}", instructions, images, context, SUGGESTIONS)
                return check_suggestions(result)
            except InvalidResponse as exc:
                if attempt == 2:
                    raise
                context = {**context, "validation_feedback": str(exc)}

    def plan(self, folder, images, context):
        instructions = """You are the prompt agent. Read the marks in their spatial context. Resolve
the selected instance using landmarks, ordering and its neighbors. If the target, action or
destination genuinely cannot be identified, ask one specific clarification question, status clarify.
For a ready plan write a precise, self-contained ENGLISH editing prompt for Qwen, with position,
appearance and details to preserve. The generator may receive a clean source with no marks.
Do not invent requirements not indicated by the user. Write testable criteria for target identity,
effect, neighbors, absence of overlaid instruction marks, and local visual quality. For movement
also require the original spot empty, the SAME object at destination and unchanged total count.
Set unused origin/destination criteria to empty strings. Preserve original scene text/signage.
Set regions_reliable only when you can trace the allowed edit footprint without including unrelated
objects. Give compact edit polygons, including required reconstruction or space for a replacement.
For a move provide separate origin and destination polygons. Points use integers 0..1000 relative
to full-image width and height, top left origin. Stroke coordinates in the context use source pixels.
Use empty regions/false where boundaries are uncertain. On clarification fill unused fields with
empty strings, lists, or false. Write the question in the language of the user's note when possible.
When approved_steps are present, they are the user's final choice. Follow every step and its
current geometry, especially any dragged destination. The note gives background. Translate each
step into the English prompt with visual landmarks. Numbered boxes and endpoints are guides."""
        return check_intent(self.call(folder, instructions, images, context, PLAN_SCHEMA))

    def revise(self, folder, images, context):
        instruction = """You are the prompt agent revising a failed edit. The initial intent and its
criteria are fixed. Use the previous visible failures and their locations to write a new ENGLISH
Qwen instruction for the actual generator input, identified in context. For a crop, use landmarks
inside the crop instead of full-frame coordinates. For cleanup only, remove annotation ink and
lettering, protecting the correct subject and other scene content. Otherwise explicitly disambiguate
the target and protected neighbors. A move must vacate the origin and keep only one copy."""
        result = self.call(folder, instruction, images, context, record(prompt=TEXT))
        if not result["prompt"].strip():
            raise InvalidResponse("The revised prompt is empty.")
        return result["prompt"]

    def review(self, folder, images, intent):
        instruction = """You are an independent visual acceptance reviewer. Inspect the actual final
candidate image, against the annotated input, clean source if present, and frozen criteria. Check
each item independently. Use pass only with positive visible evidence, fail for a visible
contradiction and unclear if you cannot tell. Identify the location and concrete evidence in the
candidate for each verdict. Validate the exact instance and protected neighbors, not just a similar
object. For a move, compare both endpoints, the object's identity and before/after counts. Inspect
the entire frame for leftover colored writing, strokes and arrows, distinguishing real scene text.
If the only defect is residual ink, other checks may pass. Bound each line of visible annotation
with one compact polygon (4 to 8 points), using candidate coordinates 0..1000. Do not trace
individual letters. Exclude correct edited objects where possible. Use [] if ink cannot be reliably
bounded. Return checks for this candidate only."""
        context = {k: intent[k] for k in ("operation", "target", "location", "destination", "preserve")}
        context["criteria"] = required_checks(intent)
        if intent.get("approved_steps"):
            context["coordinate_system"] = FRAME
        return check_report(intent, self.call(folder, instruction, images, context, report_schema(intent)))


def review_with_retry(agent, folder, images, intent):
    for index in (1, 2):
        call_dir = folder / f"review-{index}"
        try:
            if (call_dir / "invalid.txt").exists():
                raise InvalidResponse((call_dir / "invalid.txt").read_text())
            cached = call_dir / "response.json"
            if cached.is_file():
                try:
                    return check_report(intent, json.loads(cached.read_text()))
                except json.JSONDecodeError as exc:
                    raise InvalidResponse(f"Invalid JSON: {exc.msg}") from exc
            return check_report(intent, agent.review(call_dir, images, intent))
        except InvalidResponse as exc:
            call_dir.mkdir(parents=True, exist_ok=True)
            (call_dir / "invalid.txt").write_text(str(exc))
            if index == 2:
                raise
