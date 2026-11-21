"""Blindly evaluate four existing candidates, then compare against expected checks."""

import argparse
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic

from agent_qwen.vision import CodexVision, review_with_retry


PAPER = Path(__file__).resolve().parents[1] / "paper-examples"
CASES = {
    "patio": ("guided", "effect", "fail"),
    "fern": ("guided", "marks", "fail"),
    "cats": ("canvas", "origin", "fail"),
    "headphones": ("guided", "all", "pass"),
}


def run_one(task):
    name, out = task
    mode, key, expected = CASES[name]
    folder = out / name
    try:
        folder.mkdir()
    except FileExistsError:
        # A new calibration needs a fresh agent judgment. Keep earlier evidence
        # available even when the caller reuses the output directory.
        folder = Path(tempfile.mkdtemp(prefix="repeat-", dir=folder))
    images = [("user's marked canvas", PAPER / "assets" / f"{name}-canvas.webp")]
    start = monotonic()
    try:
        agent = CodexVision()
        intent = agent.plan(folder / "plan", images, {})
        if intent["status"] != "ready":
            raise ValueError(intent["question"])
        report = review_with_retry(agent, folder, images + [
            ("actual candidate to inspect", PAPER / f"{name}-{mode}.png")], intent)
        checks = {k: v["verdict"] for k, v in report["checks"].items()}
        passed = all(v == "pass" for v in checks.values())
        match = passed if key == "all" else checks.get(key) == expected and not passed
        return dict(case=name, candidate=f"{name}-{mode}.png", checks=checks,
                    expected=f"{key}={expected}", matched=match, evidence=str(folder.relative_to(out)),
                    elapsed_s=round(monotonic()-start, 2))
    except Exception as exc:
        return dict(case=name, error=str(exc), matched=False, elapsed_s=round(monotonic()-start, 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(run_one, ((name, args.out) for name in CASES)))
    report = json.dumps(rows, ensure_ascii=False, indent=2)
    (args.out / "summary.json").write_text(report)
    print(report)
    return int(any(not row["matched"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
