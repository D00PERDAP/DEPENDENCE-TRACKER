"""Meme engine regressions: no network, no user data, no copyrighted inputs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import meme_studio as ms
import meme_review_store as store
from meme_harvester import topical_score, public_https


class MemeStudioTests(unittest.TestCase):
    def test_large_original_catalog_without_duplicates(self):
        jokes = ms.catalog()
        self.assertGreaterEqual(len(jokes), 85)
        self.assertEqual(len(jokes), len(set(jokes)))
        self.assertEqual({x.category for x in jokes}, {"general", "ex", "calls", "money", "travel"})
        self.assertTrue(any("меф" in repr(x).lower() for x in jokes))
        self.assertTrue(any("техник" in repr(x).lower() for x in jokes))

    def test_every_caption_fits_panels(self):
        image = Image.new("RGB", (ms.SIZE, ms.SIZE))
        draw = ImageDraw.Draw(image)
        for joke in ms.catalog():
            ms.fit(draw, joke.label.upper(), ms.SIZE - 185, 2, start=45, floor=28, max_height=100)
            ms.fit(draw, joke.setup, ms.SIZE - 178, 3, start=67, floor=34, max_height=190)
            ms.fit(draw, joke.punchline, ms.SIZE - 178, 4, start=68, floor=30, max_height=230)

    def test_renders_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = ms.ensure_catalog(Path(tmp), limit=3)
            self.assertEqual(len(first), 3)
            self.assertTrue(all(p.exists() for p in first))
            with Image.open(first[0]) as image:
                self.assertEqual(image.size, (ms.SIZE, ms.SIZE))
                self.assertEqual(image.format, "JPEG")
            before = [p.stat().st_mtime_ns for p in first]
            after = ms.ensure_catalog(Path(tmp), limit=3)
            self.assertEqual(first, after)
            self.assertEqual(before, [p.stat().st_mtime_ns for p in after])


class StoreTests(unittest.TestCase):
    def test_concurrent_safe_updates_and_exception_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "review.json"
            self.assertEqual(store.read(index), [])
            self.assertEqual(store.transact(lambda data: data.append({"id": 1}), index), None)
            self.assertEqual(store.transact(lambda data: data.append({"id": 2}), index), None)
            def failed(data):
                data.append({"id": 3})
                raise ValueError("do not persist")
            with self.assertRaises(ValueError):
                store.transact(failed, index)
            self.assertEqual([x["id"] for x in store.read(index)], [1, 2])
            self.assertEqual(len(json.loads(index.read_text(encoding="utf-8"))), 2)

    def test_corrupted_file_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "review.json"
            index.write_text("INVALID", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.transact(lambda d: d.append({}), index)
            self.assertEqual(index.read_text(encoding="utf-8"), "INVALID")


class HarvesterAdditionalTests(unittest.TestCase):
    def test_unicode_percent_encoded_topics(self):
        self.assertGreater(topical_score("https://example.org/%D0%BC%D0%B5%D1%84.jpg"), 0)
        self.assertGreater(topical_score("https://example.org/random.jpg", "Паша Техник мем"), 0)
        self.assertEqual(topical_score("https://example.org/cat.jpg"), 0)

    def test_block_local_network(self):
        with self.assertRaises(ValueError):
            public_https("http://example.org/post")
        with self.assertRaises(ValueError):
            public_https("https://127.0.0.1/internal")
        with self.assertRaises(ValueError):
            public_https("https://user:password@example.org/")


if __name__ == "__main__":
    unittest.main()
