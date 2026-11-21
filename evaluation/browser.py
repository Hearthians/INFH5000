"""Exercise the running canvas with Chromium, including refresh and a real download."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:7865")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--example", choices=["chair", "suitcase"], default="chair")
    parser.add_argument("--resume-job", help="Continue an interrupted browser verification by its saved ID")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    if args.example == "suitcase":
        source = root / "paper-examples/assets/method-source.webp"
        corners = ((0.46, 0.47), (0.80, 0.97))
        label, anchor = "Replace with one red suitcase", (0.47, 0.24)
        answer = "Replace only the blue folding chair with one red suitcase."
    else:
        source = root / "blue-chair/assets/chair-source.webp"
        corners = ((0.365, 0.39), (0.623, 0.87))
        label, anchor = "Recolor to velvet blue", (0.36, 0.24)
        answer = "Recolor only the middle chair's upholstery to velvet blue."
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("AGENT_QWEN_CHROMIUM"))
        page = browser.new_page(viewport={"width": 1280, "height": 980}, accept_downloads=True)
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        if args.resume_job:
            submitted = args.resume_job
            page.goto(args.url+"/#"+submitted)
            page.locator("#saved-canvas").wait_for(state="visible")
            if page.locator("#status").get_attribute("data-kind") == "error":
                with page.expect_response(lambda r: r.url.endswith("/continue")) as event:
                    page.locator("#reconnect").click()
                assert event.value.status == 202
                page.wait_for_function("() => document.querySelector('#status').dataset.kind !== 'error'")
        else:
            page.goto(args.url)
            page.locator("#upload").set_input_files(source)
            canvas = page.locator("#drawing")
            canvas.wait_for(state="visible")
            box = canvas.bounding_box()

            def position(x, y):
                return (box["x"] + x*box["width"], box["y"] + y*box["height"])

            def drag(tool, start, end):
                page.locator(f'[data-tool="{tool}"]').click()
                page.mouse.move(*position(*start))
                page.mouse.down()
                page.mouse.move(*position(*end), steps=8)
                page.mouse.up()

            for tool in ("pen", "arrow"):
                drag(tool, (0.2,0.7), (0.4,0.8))
                page.locator("#undo").click()
            drag("circle", *corners)
            page.locator('[data-tool="text"]').click()
            page.locator("#mark-text").fill(label)
            # The text toolbar changes the canvas position.
            box = canvas.bounding_box()
            page.mouse.click(*position(*anchor))
            with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as event:
                page.locator("#generate").click()
            submitted = event.value.json()["id"]
            print(f"Browser job {submitted}", flush=True)
            page.wait_for_url("**/#"+submitted)
        page.reload()
        page.locator("#saved-canvas").wait_for(state="visible")
        page.wait_for_function("""() => ['passed','exhausted','error','needs_clarification']
            .includes(document.querySelector('#status').dataset.kind)""", timeout=1800000)
        if page.locator("#status").get_attribute("data-kind") == "needs_clarification":
            page.locator("#answer").fill(answer)
            page.locator("#clarify button").click()
            page.wait_for_function("""() => ['passed','exhausted','error']
                .includes(document.querySelector('#status').dataset.kind)""", timeout=1800000)
        state = page.request.get(args.url+"/api/jobs/"+submitted).json()
        if state["status"] == "error" or not state["best"]:
            raise RuntimeError(state["error"] or state["status"])
        number = int(page.locator("#attempt").input_value())
        assert number == state["best"] and page.locator("#checks li").count() >= 5
        with page.expect_download() as event:
            page.locator("#download").click()
        destination = args.out / "download.png"
        event.value.save_as(destination)
        data = destination.read_bytes()
        expected = page.request.get(f"{args.url}/api/jobs/{submitted}/candidate/{number}").body()
        assert data == expected
        with Image.open(destination) as image:
            image.verify()
        page.screenshot(path=str(args.out / "result.png"), full_page=True)
        result = dict(example=args.example, job_id=submitted, status=state["status"], best=number,
                      prompt_ids=[a["prompt_id"] for a in state["attempts"]],
                      source_size=state["size"], download_sha256=hashlib.sha256(data).hexdigest(),
                      downloaded_bytes=len(data), browser_errors=errors, restored_after_reload=True, resumed_after_error=bool(args.resume_job))
        (args.out / "verification.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
        browser.close()
        assert not errors


if __name__ == "__main__":
    main()
