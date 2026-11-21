import json
import re
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, abort, jsonify, request, send_file
from PIL import Image

from .engine import Runner, queue_continuation
from .storage import create_job, job_path, read_json


def create_app(root, runner=Runner):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 128 * 1024 * 1024
    root.mkdir(parents=True, exist_ok=True)
    workers = ThreadPoolExecutor(max_workers=2)
    def schedule(job):
        workers.submit(runner(root / job).run)

    def load(job):
        if not re.fullmatch("[0-9a-f]{32}", job) or not (root / job / "state.json").is_file():
            abort(404, "未找到该任务")
        return read_json(job_path(root, job) / "state.json")

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    def error_response(error):
        return jsonify(error=error.description), error.code

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/review")
    def review_sheet():
        from evaluation.review import HERE
        sheet = HERE / "review.html"
        if not sheet.is_file():
            abort(404, "尚未生成事后复核页")
        return send_file(sheet)

    @app.get("/review/reference/<case>")
    def review_reference(case):
        from evaluation.review import HERE, reference
        if not (HERE / "batch.json").is_file() or case not in {r["case"] for r in read_json(HERE / "batch.json")["cases"]}:
            abort(404)
        file = reference(case)
        if not file.is_file():
            abort(404)
        return send_file(file)

    @app.post("/api/jobs")
    def submit():
        if "canvas" not in request.files:
            abort(400, "请上传标注图")
        try:
            job = create_job(root, request.files["canvas"], source=request.files.get("source"),
                             ink=request.files.get("ink"), note=request.form.get("note", ""),
                             strokes=json.loads(request.form["strokes"]) if "strokes" in request.form else None,
                             max_tries=int(request.form.get("max_tries", 3)))
        except (ValueError, OSError, Image.DecompressionBombError) as exc:
            abort(400, str(exc))
        schedule(job)
        return jsonify(id=job), 202

    @app.get("/api/jobs/<job>")
    def status(job):
        response = jsonify(load(job))
        response.cache_control.no_store = True
        return response

    @app.post("/api/jobs/<job>/continue")
    def resume(job):
        state = load(job)
        answer = request.form.get("answer", "").strip()
        if state["status"] == "needs_clarification" and not answer:
            abort(400, "请先回答澄清问题")
        if state["status"] not in {"needs_clarification", "error"}:
            abort(409, "任务当前不需要继续")
        try:
            queue_continuation(root / job, answer or None)
        except (ValueError, BlockingIOError) as exc:
            abort(409, str(exc))
        schedule(job)
        return jsonify(id=job), 202

    @app.get("/api/jobs/<job>/canvas")
    def canvas(job):
        load(job)
        return send_file(root / job / "inputs/canvas.png", mimetype="image/png")

    @app.get("/api/jobs/<job>/candidate/<int:number>")
    def candidate(job, number):
        state = load(job)
        if number < 1 or number > len(state["attempts"]):
            abort(404)
        path = state["attempts"][number-1]["candidate"]
        if not path:
            abort(404)
        return send_file(root / job / path, mimetype="image/png",
                         as_attachment=request.args.get("download") == "1",
                         download_name=f"{job}-attempt-{number}.png")

    for directory in root.iterdir():
        if re.fullmatch("[0-9a-f]{32}", directory.name) and (directory / "state.json").is_file():
            if read_json(directory / "state.json")["status"] in {"running", "queued"}:
                schedule(directory.name)
    return app
