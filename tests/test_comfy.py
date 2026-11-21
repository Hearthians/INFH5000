import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from PIL import Image

from agent_qwen.comfy import Comfy


class TransportTests(unittest.TestCase):
    @patch("agent_qwen.comfy.time.sleep")
    def test_lost_post_reply_and_transient_history_failure_use_one_prompt_id(self, _sleep):
        api = Comfy("http://generator")
        api.session = Mock()
        api.session.post.side_effect = requests.ReadTimeout("lost acknowledgement")
        saved = "79ce37eb-77ef-479a-b8e0-a51277e1e942"
        self.assertEqual(api.submit({}, saved), saved)
        blank = Mock(status_code=200)
        blank.json.return_value = {}
        complete = Mock(status_code=200)
        image = {"filename": "out.png", "type": "output", "subfolder": "job"}
        complete.json.return_value = {saved: {"status": {"status_str": "success"},
                                              "outputs": {"9": {"images": [image]}}}}
        buffer = io.BytesIO()
        Image.new("RGB", (8,8), "green").save(buffer, format="PNG")
        picture = Mock(status_code=200, content=buffer.getvalue())
        api.session.get.side_effect = [requests.ConnectionError(), blank, complete, picture]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.png"
            api.collect(saved, path)
            self.assertEqual(path.read_bytes(), buffer.getvalue())
        self.assertEqual(api.session.post.call_count, 1)
        self.assertEqual(api.session.post.call_args.kwargs["json"]["prompt_id"], saved)
        urls = [call.args[0] for call in api.session.get.call_args_list]
        self.assertEqual(urls, ["http://generator/history/"+saved]*3 + ["http://generator/view"])
