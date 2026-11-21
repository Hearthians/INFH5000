"""Run the existing Qwen-Image-2.1 graph on the local ComfyUI HTTP API."""

import json
import os
import time
from pathlib import Path

import requests
from PIL import Image


WORKFLOW = Path(__file__).resolve().parents[1] / "blue-chair" / "canvas.api.json"


class SubmissionRejected(RuntimeError):
    """Comfy definitively refused the graph before queueing it."""


class Comfy:
    def __init__(self, url=None):
        self.url = (url or os.environ.get("AGENT_QWEN_COMFY_URL", "http://127.0.0.1:28188")).rstrip("/")
        self.timeout = float(os.environ.get("AGENT_QWEN_COMFY_TIMEOUT", "900"))
        self.session = requests.Session()
        self.session.trust_env = False

    def workflow(self, source, prompt, seed, prefix):
        with source.open("rb") as stream:
            response = self.session.post(self.url + "/upload/image",
                                         files={"image": ("input.png", stream, "image/png")},
                                         data={"subfolder": prefix, "type": "input", "overwrite": "true"},
                                         timeout=30)
        response.raise_for_status()
        uploaded = response.json()
        graph = json.loads(WORKFLOW.read_text())
        graph["5"]["inputs"]["prompt"] = prompt
        graph["6"]["inputs"]["image"] = "/".join(filter(None, (uploaded.get("subfolder"), uploaded["name"])))
        graph["7"]["inputs"]["seed"] = seed
        graph["9"]["inputs"]["filename_prefix"] = prefix + "/result"
        return graph

    def submit(self, graph, prompt_id):
        # Current Comfy accepts a client-assigned UUID. A lost HTTP reply is ambiguous:
        # polling that pre-saved UUID is the only safe follow-up.
        try:
            response = self.session.post(self.url + "/prompt",
                                         json={"prompt": graph, "prompt_id": prompt_id, "client_id": prompt_id},
                                         timeout=30)
        except (requests.ConnectionError, requests.Timeout):
            return prompt_id
        if response.status_code >= 500:
            return prompt_id
        if not response.ok:
            raise SubmissionRejected(f"ComfyUI rejected the workflow ({response.status_code}): {response.text[:900]}")
        try:
            return response.json()["prompt_id"]
        except (ValueError, KeyError):
            return prompt_id

    def _get(self, path, deadline, params=None):
        for retry in range(5):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ComfyUI polling deadline reached.")
            try:
                response = self.session.get(self.url + path, params=params, timeout=min(30, remaining))
                if response.status_code < 500 and response.status_code != 429:
                    response.raise_for_status()
                    return response
                error = f"HTTP {response.status_code}"
            except (requests.ConnectionError, requests.Timeout) as exc:
                error = str(exc)
            if retry < 4:
                time.sleep(min(2, max(0, deadline-time.monotonic())))
        raise RuntimeError(f"ComfyUI unavailable while reading {path}: {error}")

    def collect(self, prompt_id, destination):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            response = self._get(f"/history/{prompt_id}", deadline)
            entry = response.json().get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") != "success":
                    details = [d.get("exception_message", name) for name, d in status.get("messages", [])
                               if name in {"execution_error", "execution_interrupted"}]
                    raise RuntimeError(f"ComfyUI job {prompt_id} failed: {details or status.get('status_str')}")
                image = entry["outputs"]["9"]["images"][0]
                content = self._get("/view", deadline, params=image).content
                tmp = destination.with_suffix(".part")
                tmp.write_bytes(content)
                with Image.open(tmp) as im:
                    im.verify()
                tmp.replace(destination)
                return
            time.sleep(min(2, max(0, deadline-time.monotonic())))
        raise TimeoutError(f"Timed out waiting for ComfyUI prompt {prompt_id}.")
