import argparse
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .engine import Runner, failures
from .storage import create_job, job_path, read_json, write_json


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def summary(state, root):
    best = state["best"]
    return dict(id=state["id"], status=state["status"], generations=state["generation_count"],
                first_pass=bool(state["attempts"] and state["attempts"][0]["review"]
                                and not failures(state["attempts"][0]["review"])),
                best=best, candidate=str(root / state["id"] / state["attempts"][best-1]["candidate"])
                if best else None, remaining=state["remaining"], question=state["question"],
                error=state["error"], elapsed_s=state["elapsed_s"])


def run_batch(args, root):
    manifest = read_json(args.manifest)
    cases = manifest["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("The manifest needs a non-empty cases array.")
    allowed = {"id", "canvas", "source", "ink", "strokes", "note", "max_tries"}
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or set(case)-allowed or not case.get("canvas") or not case.get("id"):
            raise ValueError("Each case needs id and canvas and may contain only user input fields.")
        if case["id"] in ids:
            raise ValueError(f"Duplicate case ID: {case['id']}")
        ids.add(case["id"])
    folder = args.manifest.resolve().parent

    def run_case(case):
        data = {k: folder / case[k] for k in ("canvas", "source", "ink") if case.get(k)}
        if case.get("strokes"):
            data["strokes"] = read_json(folder / case["strokes"])
        job = create_job(root, **data, note=case.get("note", ""),
                         max_tries=case.get("max_tries", args.max_tries))
        print(f"{case['id']} {job}", file=sys.stderr, flush=True)
        result = summary(Runner(root / job).run(), root)
        print(f"{case['id']} {result['status']} ({result['generations']})", file=sys.stderr, flush=True)
        return {"case": case["id"], **result}

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        rows = list(workers.map(run_case, cases))
    n = len(rows)
    report = dict(cases=rows, case_count=n, first_pass=sum(r["first_pass"] for r in rows),
                  final_pass=sum(r["status"] == "passed" for r in rows),
                  mean_generations=round(sum(r["generations"] for r in rows)/n, 3),
                  mean_seconds=round(sum(r["elapsed_s"] for r in rows)/n, 2),
                  wall_seconds=round(time.monotonic()-started, 2))
    output = args.report or root / f"batch-{uuid.uuid4().hex}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    print(output)
    return 2 if any(r["status"] == "error" for r in rows) else int(report["final_pass"] != n)


def main():
    parser = argparse.ArgumentParser(prog="python -m agent_qwen")
    modes = parser.add_subparsers(dest="command", required=True)
    edit = modes.add_parser("edit", help="Edit a canvas or reconnect to a saved job")
    entry = edit.add_mutually_exclusive_group(required=True)
    entry.add_argument("--canvas", type=Path)
    entry.add_argument("--resume", metavar="JOB_ID")
    edit.add_argument("--source", type=Path)
    edit.add_argument("--ink", type=Path)
    edit.add_argument("--strokes", type=Path)
    edit.add_argument("--note", default="")
    edit.add_argument("--answer", help="Reply to a saved job's clarification question")
    batch = modes.add_parser("batch", help="Run an input-only JSON case manifest")
    batch.add_argument("manifest", type=Path)
    batch.add_argument("--workers", type=positive, default=1)
    batch.add_argument("--report", type=Path)
    serve = modes.add_parser("serve", help="Start the local drawing canvas")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=7865)
    for sub in (edit, batch):
        sub.add_argument("--max-tries", type=positive, default=3)
    for sub in (edit, batch, serve):
        sub.add_argument("--runs", type=Path, default=Path(os.environ.get("AGENT_QWEN_RUNS", "runs")))
    args = parser.parse_args()
    root = args.runs.resolve()
    try:
        if args.command == "serve":
            from .web import create_app
            create_app(root).run(host=args.host, port=args.port, threaded=True, use_reloader=False)
            return 0
        if args.command == "batch":
            return run_batch(args, root)
        if args.answer and not args.resume:
            parser.error("--answer requires --resume")
        job = args.resume or create_job(root, args.canvas, source=args.source, ink=args.ink,
                                        strokes=read_json(args.strokes) if args.strokes else None,
                                        max_tries=args.max_tries, note=args.note)
        print(f"Job {job}", file=sys.stderr, flush=True)
        state = Runner(job_path(root, job)).run(answer=args.answer, resume_error=bool(args.resume))
        print(json.dumps(summary(state, root), ensure_ascii=False, indent=2))
        return 0 if state["status"] == "passed" else (2 if state["status"] == "error" else 1)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    sys.exit(main())
