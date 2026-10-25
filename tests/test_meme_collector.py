"""Regression tests for the meme pipeline. Run: python -m unittest discover -s tests -v"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import meme_collector as mc


class CollectorTests(unittest.TestCase):
    def test_image_metadata(self):
        parser = mc.ImageMetadata()
        parser.feed('<meta property="og:image" content="https://example.org/a.jpg"><img src="https://example.org/b.jpg">')
        self.assertEqual(parser.images, ["https://example.org/a.jpg", "https://example.org/b.jpg"])

    def test_ignore_relative_and_non_https_images(self):
        parser = mc.ImageMetadata()
        parser.feed('<img src="/relative.jpg"><img src="http://example.org/a.jpg">')
        self.assertEqual(parser.images, [])

    def test_candidate_only_pending(self):
        items = [{"id": 1, "status": "pending"}, {"id": 2, "status": "rejected"}]
        self.assertEqual(mc.candidate(items, 1)["id"], 1)
        with self.assertRaises(SystemExit):
            mc.candidate(items, 2)

    def test_roundtrip_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            with patch.object(mc, "INDEX", path):
                self.assertEqual(mc.load(), [])
                mc.save([{"id": 1, "title": "Тест", "status": "pending"}])
                self.assertEqual(mc.load()[0]["title"], "Тест")

    def test_reject_duplicate_candidate(self):
        items = [{"id": 3, "status": "approved"}]
        with self.assertRaises(SystemExit):
            mc.candidate(items, 3)


if __name__ == "__main__":
    unittest.main()
