import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from agent_qwen.comfy import SubmissionRejected
from agent_qwen.engine import Runner, queue_continuation
from agent_qwen.regions import edit_polygons
from agent_qwen.storage import create_job, read_json
from agent_qwen.vision import BASE_CHECKS, MOVE_CHECKS, required_checks


def polygon(x, y):
    return [dict(x=x, y=y), dict(x=x+160, y=y), dict(x=x+160, y=y+190), dict(x=x, y=y+190)]


def intention(move=False):
    return dict(status="ready", question="", operation="move" if move else "modify",
                target="selected tile", location="upper left", destination="lower right" if move else "",
                preserve=["neighbor"], regions_reliable=True,
                regions=[dict(role="origin" if move else "edit", points=polygon(100, 150))]
                + ([dict(role="destination", points=polygon(720, 680))] if move else []),
                criteria={k: "Observe " + k for k in BASE_CHECKS + MOVE_CHECKS}, prompt="Edit the selected tile.")


def report(intent, **verdicts):
    return dict(checks={k: dict(verdict=verdicts.get(k, "pass"), location="left", evidence="visible pixels")
                        for k in required_checks(intent)}, ink_regions=[])


class Generator:
    def __init__(self):
        self.ids = []
        self.polls = []
        self.timeout_once = False

    def workflow(self, source, prompt, seed, prefix):
        return {"seed": seed, "source": str(source)}

    def submit(self, graph, prompt_id):
        self.ids.append(prompt_id)
        return prompt_id

    def collect(self, prompt_id, destination):
        self.polls.append(prompt_id)
        if self.timeout_once:
            self.timeout_once = False
            raise TimeoutError("lost connection")
        Image.new("RGB", (80, 64), "darkslateblue").save(destination)


class LoopTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.image = self.root / "image.png"
        Image.new("RGB", (120, 90), "white").save(self.image)
        self.intent = intention()
        self.agent = Mock()
        self.agent.plan.return_value = self.intent
        self.agent.revise.return_value = "Revise this specific edit."
        self.gpu = Generator()

    def setup_job(self, reports, *, clean=True, limit=3):
        job = create_job(self.root, self.image, source=self.image if clean else None, max_tries=limit)
        self.agent.review.side_effect = reports
        self.directory = self.root / job
        self.runner = Runner(self.directory, self.agent, self.gpu)
        return self.runner

    def test_first_pass_clean_source_and_seed(self):
        state = self.setup_job([report(self.intent)]).run()
        self.assertEqual((state["status"], state["best"], state["generation_count"]), ("passed", 1, 1))
        attempt = state["attempts"][0]
        self.assertEqual((attempt["input"], attempt["seed"], attempt["steps"]), ("inputs/source.png", 42, 40))
        self.assertTrue((self.directory / attempt["candidate"]).exists())

    def test_move_retries_from_source_and_preserves_other_pixels(self):
        self.intent = intention(move=True)
        self.agent.plan.return_value = self.intent
        state = self.setup_job([report(self.intent, origin="fail"), report(self.intent)]).run()
        attempt = state["attempts"][1]
        self.assertEqual((state["status"], attempt["mode"], state["generation_count"]), ("passed", "crop", 2))
        self.assertIn("origin", attempt["feedback"][0]["failed_checks"])
        self.assertEqual(attempt["input"], "attempt-2/crop.png")
        self.assertEqual(self.agent.plan.call_count, 1)
        with Image.open(self.directory / attempt["candidate"]) as im:
            self.assertEqual(im.size, (120, 90))
            for p in [(0, 0), (60, 45), (110, 3)]:
                self.assertEqual(im.getpixel(p), (255,255,255))
            for p in [(19, 20), (94, 68)]:
                self.assertNotEqual(im.getpixel(p), (255,255,255))
        missing = copy.deepcopy(self.intent)
        missing["regions"].pop()
        self.assertEqual(edit_polygons(missing), [])

    def test_cleanup_from_subject_passed_candidate_and_full_review(self):
        ink = report(self.intent, marks="fail")
        ink["ink_regions"] = [polygon(70, 70)]
        state = self.setup_job([ink, report(self.intent)], clean=False).run()
        first, second = state["attempts"]
        self.assertEqual((second["mode"], second["input"], second["parent_attempt"]),
                         ("cleanup", first["candidate"], 1))
        self.assertEqual(state["generation_count"], 2)
        self.assertEqual(first["input"], "inputs/canvas.png")
        self.assertTrue(self.agent.review.call_args.args[1][-1][1].samefile(self.directory / second["candidate"]))

    def test_regression_keeps_best_and_cleanup_is_not_repeated(self):
        values = [report(self.intent, marks="fail"), report(self.intent, target="fail", effect="fail"),
                  report(self.intent, target="fail", effect="fail", neighbors="unclear")]
        state = self.setup_job(values).run()
        self.assertEqual((state["status"], state["best"], state["generation_count"]), ("exhausted", 1, 3))
        self.assertEqual(list(state["remaining"]), ["marks"])
        self.assertTrue(all(a["regressed"] for a in state["attempts"][1:]))
        self.assertEqual(state["attempts"][2]["mode"], "crop")
        self.assertEqual(state["attempts"][2]["base"], "inputs/source.png")

    def test_unclear_blocks_delivery_and_limit_counts_first(self):
        state = self.setup_job([report(self.intent, quality="unclear")], limit=1).run()
        self.assertEqual((state["status"], state["generation_count"]), ("exhausted", 1))
        self.assertEqual(state["remaining"]["quality"]["verdict"], "unclear")

    def test_malformed_verdict_retries_only_review(self):
        state = self.setup_job([{"checks": {}}, report(self.intent)]).run()
        self.assertEqual((state["status"], self.agent.review.call_count, len(self.gpu.ids)), ("passed", 2, 1))
        self.assertTrue((self.directory / "attempt-1/review-1/invalid.txt").exists())

    def test_persistent_invalid_and_service_fault(self):
        for results, calls in [([{}, {}], 2), ([TimeoutError("vision timeout")], 1)]:
            with self.subTest(calls=calls):
                self.agent.review.reset_mock()
                state = self.setup_job(results).run()
                self.assertEqual((state["status"], self.agent.review.call_count, state["generation_count"]),
                                 ("error", calls, 1))
                self.assertIsNone(state["best"])
                self.assertTrue(state["error"])

    def test_clarification_consumes_zero_then_same_job_continues(self):
        clarify = dict(self.intent, status="clarify", question="Which of the two tiles?")
        self.agent.plan.side_effect = [clarify, self.intent]
        runner = self.setup_job([report(self.intent)])
        state = runner.run()
        self.assertEqual((state["status"], state["generation_count"]), ("needs_clarification", 0))
        state = runner.run(answer="The left one")
        self.assertEqual(state["status"], "passed")
        self.assertEqual(self.agent.plan.call_args.args[2]["clarifications"][0]["answer"], "The left one")

    def test_reconnect_polls_saved_id_without_posting(self):
        self.gpu.timeout_once = True
        runner = self.setup_job([report(self.intent)])
        self.assertEqual(runner.run()["status"], "error")
        saved = read_json(self.directory / "state.json")["attempts"][0]["prompt_id"]
        state = Runner(self.directory, self.agent, self.gpu).run(resume_error=True)
        self.assertEqual((state["status"], state["generation_count"]), ("passed", 1))
        self.assertEqual(self.gpu.ids, [saved])
        self.assertEqual(self.gpu.polls, [saved, saved])

    def test_queued_web_answer_survives_worker_restart(self):
        self.agent.plan.side_effect = [dict(self.intent, status="clarify", question="Which tile?"), self.intent]
        runner = self.setup_job([report(self.intent)])
        runner.run()
        queue_continuation(self.directory, "Right tile")
        state = read_json(self.directory / "state.json")
        self.assertEqual(state["status"], "queued")
        self.assertEqual(state["answers"][-1]["answer"], "Right tile")
        self.assertEqual(Runner(self.directory, self.agent, self.gpu).run()["status"], "passed")

    def test_explicit_queue_rejection_is_not_counted_as_generation(self):
        self.gpu.submit = Mock(side_effect=SubmissionRejected("unknown node"))
        runner = self.setup_job([])
        state = runner.run()
        self.assertEqual((state["status"], state["generation_count"]), ("error", 0))
        self.assertIsNone(state["attempts"][0]["submitted_at"])
        self.assertTrue(state["attempts"][0]["rejected_submissions"][0]["prompt_id"])
        self.assertEqual(state["errors"][-1]["stage"], "submitting")

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            create_job(self.root, self.image, max_tries=0)
        with self.assertRaises(ValueError):
            create_job(self.root, self.image, strokes={"width": 60, "height": 45, "strokes": []})
        self.assertFalse(list(self.root.glob(".new-*")))


if __name__ == "__main__":
    unittest.main()
