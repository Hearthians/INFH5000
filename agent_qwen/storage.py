"""On-disk job state, oriented inputs and source-coordinate pen strokes."""

import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from .proposals import check_proposal


def write_json(path, value):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        name = stream.name
    os.replace(name, path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def job_path(root, job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Invalid job ID.")
    return root / job_id


def save_input(source, path, mode):
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image.convert(mode).save(path, format="PNG")
        return image.size


def check_strokes(data, size):
    if not isinstance(data, dict) or [data.get("width"), data.get("height")] != list(size):
        raise ValueError("Stroke dimensions must match the original image.")
    if not isinstance(data.get("strokes"), list):
        raise ValueError("Expected an array of strokes.")
    for stroke in data["strokes"]:
        if not isinstance(stroke, dict) or stroke.get("tool") not in {"circle", "arrow", "pen", "text"}:
            raise ValueError("Unknown drawing tool.")
        points = stroke.get("points")
        if not isinstance(points, list) or not points:
            raise ValueError("A stroke needs at least one point.")
        if stroke["tool"] in {"arrow", "circle"} and len(points) != 2:
            raise ValueError("An arrow or circle needs its two endpoints.")
        if stroke["tool"] == "text" and not isinstance(stroke.get("text"), str):
            raise ValueError("A text annotation needs text.")
        for p in points:
            for key, limit in zip(("x", "y"), size):
                v = p.get(key) if isinstance(p, dict) else None
                if type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= limit:
                    raise ValueError("Stroke coordinates must be finite and inside the image.")


def create_job(root, canvas, *, source=None, ink=None, strokes=None, note="", max_tries=3, proposal=None):
    if type(max_tries) is not int or max_tries < 1:
        raise ValueError("max_tries must be a positive integer.")
    root.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    directory = Path(tempfile.mkdtemp(prefix=".new-", dir=root))
    inputs = directory / "inputs"
    try:
        inputs.mkdir()
        marked_size = save_input(canvas, inputs / "canvas.png", "RGB")
        size = save_input(source, inputs / "source.png", "RGB") if source is not None else marked_size
        aspect_ratio = (marked_size[0] * size[1]) / (marked_size[1] * size[0])
        if abs(aspect_ratio - 1) > 0.03:
            raise ValueError("Canvas and source must show the same framing and aspect ratio.")
        if ink is not None and save_input(ink, inputs / "ink.png", "RGBA") != size:
            raise ValueError("Ink dimensions must match the original image.")
        if strokes is None:
            strokes = {"width": size[0], "height": size[1], "strokes": []}
        check_strokes(strokes, size)
        write_json(inputs / "strokes.json", strokes)
        state = dict(id=job_id, status="queued", stage="queued", max_tries=max_tries,
                     size=list(size), canvas_size=list(marked_size), has_source=source is not None,
                     has_ink=ink is not None, note=note, answers=[], question=None, intent=None,
                     attempts=[], best=None, remaining={}, generation_count=0,
                     created_at=time.time(), elapsed_s=0, error=None)
        if proposal is not None:
            state["proposal"] = check_proposal(proposal)
            write_json(inputs / "proposal.json", proposal)
        write_json(directory / "state.json", state)
        directory.rename(root / job_id)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return job_id


def user_images(directory, state):
    inputs = directory / "inputs"
    images = [("user's annotated full canvas", inputs / "canvas.png")]
    if state["has_source"]:
        images.append(("clean original, before editing", inputs / "source.png"))
    return images
