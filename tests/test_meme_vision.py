"""Paid AI vision prefilter tests: mocked Responses API, never uses network."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import meme_vision as vision


class VisionTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict("os.environ", {"MEME_AI_VISION": "0", "OPENAI_API_KEY": "fake"}):
            self.assertFalse(vision.enabled())
            self.assertIsNone(vision.analyze("/path/that/does/not/exist"))

    def test_blocks_untrusted_local_path(self):
        with patch.dict("os.environ", {"MEME_AI_VISION": "1", "OPENAI_API_KEY": "fake"}):
            with self.assertRaises(ValueError):
                vision.analyze("/etc/passwd")

    def test_structured_image_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            preview = Path(tmp) / "sample.jpg"
            preview.write_bytes(b"pretend image data")
            payload = {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps({
                    "on_topic": True, "humor": 4, "readable": True, "reason": "ирония"})}
            ]}]}
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, *args): return json.dumps(payload).encode()
            with patch.dict("os.environ", {"MEME_AI_VISION": "1", "OPENAI_API_KEY": "fake"}):
                with patch.object(vision, "REVIEW", Path(tmp)):
                    with patch("meme_vision.urllib.request.urlopen", return_value=Response()) as mocked:
                        result = vision.analyze(preview)
            self.assertEqual(result["humor"], 4)
            sent = json.loads(mocked.call_args.args[0].data)
            content = sent["input"][0]["content"]
            self.assertEqual(content[1]["type"], "input_image")
            self.assertEqual(sent["text"]["format"]["type"], "json_schema")


if __name__ == "__main__":
    unittest.main()
