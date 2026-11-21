"""Run both blue-chair workflows against the local ComfyUI HTTP API."""
import json
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
SERVER = "http://127.0.0.1:8188"
session = requests.Session()
session.trust_env = False


def run(name):
    graph = json.loads((ROOT / f"{name}.api.json").read_text())
    image_path = Path(graph["6"]["inputs"]["image"])
    run_workflow(graph, ROOT / "assets" / image_path.name, ROOT / f"{name}.png")


def run_workflow(graph, source_path, output_path):
    name = output_path.stem
    image_path = Path(graph["6"]["inputs"]["image"])
    with source_path.open("rb") as source:
        response = session.post(
            f"{SERVER}/upload/image",
            files={"image": (image_path.name, source, "image/webp")},
            data={"type": "input", "subfolder": str(image_path.parent)},
            timeout=30,
        )
    response.raise_for_status()
    uploaded = response.json()
    graph["6"]["inputs"]["image"] = str(Path(uploaded["subfolder"]) / uploaded["name"])
    response = session.post(f"{SERVER}/prompt", json={"prompt": graph}, timeout=30)
    if not response.ok:
        raise RuntimeError(response.text)
    prompt_id = response.json()["prompt_id"]
    print(f"{name}: queued {prompt_id}", flush=True)
    while True:
        response = session.get(f"{SERVER}/history/{prompt_id}", timeout=30)
        response.raise_for_status()
        record = response.json().get(prompt_id)
        if record:
            break
        time.sleep(3)
    if record["status"]["status_str"] != "success":
        errors = [data.get("exception_message", event) for event, data in record["status"]["messages"] if event.startswith("execution_") and event not in ("execution_start", "execution_cached")]
        raise RuntimeError(f"{name}: {errors}")
    image = record["outputs"]["9"]["images"][0]
    response = session.get(f"{SERVER}/view", params=image, timeout=30)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    print(f"{name}: saved {output_path.name}", flush=True)


def make_comparison():
    width, height, gap = 540, 360, 20
    board = Image.new("RGB", (3 * width + 4 * gap, 2 * (height + 55) + gap), "white")
    draw = ImageDraw.Draw(board)
    font = ImageFont.truetype("DejaVuSans.ttf", 22)
    rows = [
        [("Original", "assets/chair-source.webp"), ("Canvas instruction", "assets/chair-canvas.webp")],
        [("VibeEdit | Authors' reference", "assets/chair-output.webp"),
         ("Qwen 2.1 | Canvas only", "canvas.png"), ("Qwen 2.1 | Text instruction", "text.png")],
    ]
    for row, panels in enumerate(rows):
        left = (board.width - len(panels) * width - (len(panels) - 1) * gap) // 2
        top = gap + row * (height + 55)
        for index, (label, filename) in enumerate(panels):
            x = left + index * (width + gap)
            draw.text((x, top), label, fill="#162028", font=font)
            with Image.open(ROOT / filename) as source:
                preview = ImageOps.contain(source.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
                board.paste(preview, (x + (width - preview.width) // 2, top + 36))
    board.save(ROOT / "comparison.png")


if __name__ == "__main__":
    for experiment in ("canvas", "text"):
        run(experiment)
    make_comparison()
