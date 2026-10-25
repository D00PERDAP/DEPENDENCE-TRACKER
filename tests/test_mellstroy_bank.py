"""600 actual stream-frame pipeline: real photos, rights, dedup, mood routing.

All image samples are synthetic COLOR TEST PATTERNS, never impersonations.
They only test processing. Real Mellstroy photos must come from authorized clips.
"""
from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import mellstroy_bank as bank


def test_photo(path: Path, seed: int):
    rng = random.Random(seed)
    image = Image.new("RGB", (440, 330), (rng.randrange(75, 230), 30, 80))
    draw = ImageDraw.Draw(image)
    for _ in range(60):
        x, y = rng.randrange(0, 440), rng.randrange(0, 330)
        d = rng.randrange(2, 40)
        draw.rectangle((x, y, x+d, y+d),
                       fill=(rng.randrange(255), rng.randrange(255), rng.randrange(255)))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=88)


class FrameBankTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for field, dest in (( "ROOT", root), ("BANK", root / "data" / "mellstroy_frames"),
                            ("CATALOG", root / "data" / "mellstroy_frames" / "catalog.json")):
            patcher = patch.object(bank, field, dest)
            patcher.start()
            self.addCleanup(patcher.stop)
        bank.INDEX_CACHE.update(mtime=None, items=[])
        self.root = root

    def test_rights_acknowledgement_is_mandatory(self):
        with self.assertRaises(PermissionError):
            bank.import_media(self.root, confirm_rights=False)

    def test_import_real_photo_framing_without_facemorphing(self):
        source = self.root / "inputs" / "shock"
        for i in range(10):
            test_photo(source / f"frame_{i}.jpg", i+20)
        results = bank.import_media(source, target=8, confirm_rights=True,
                                    min_distance=0, source_credit="permission-test")
        self.assertEqual(results["total"], 8)
        self.assertEqual(len(bank._index()), 8)
        for item in bank._index():
            self.assertEqual(item["mood"], "shock")
            self.assertTrue(item["rights_confirmed"])
            self.assertIn("permission-test", item["credit"])
            self.assertTrue((bank.BANK / item["file"]).exists())
            with Image.open(bank.BANK / item["file"]) as img:
                self.assertEqual(img.size, (440, 330))
                self.assertEqual(img.format, "JPEG")

    def test_visually_identical_images_are_not_imported_twice(self):
        parent = self.root / "faces"
        test_photo(parent / "a.jpg", 333)
        (parent / "b.jpg").write_bytes((parent / "a.jpg").read_bytes())
        result = bank.import_media(parent, target=600, confirm_rights=True)
        self.assertEqual(result["total"], 1)
        self.assertGreaterEqual(result["rejected"], 1)

    def test_situation_selects_relevant_editorial_mood(self):
        for mood, seed in (("shock", 1), ("yell", 2), ("tired", 3), ("smug", 4)):
            photo = self.root / "material" / mood / "face.jpg"
            test_photo(photo, seed)
            bank.import_media(photo, target=600, confirm_rights=True, min_distance=0)
        self.assertEqual(bank.templates("money", limit=4)[0]["mood"], "shock")
        self.assertEqual(bank.templates("contacts", limit=4)[0]["mood"], "yell")
        self.assertEqual(bank.templates("sleep", limit=4)[0]["mood"], "tired")

    def test_recently_seen_picture_is_not_selected_until_pool_exhausted(self):
        source = self.root / "material" / "laugh"
        for i in range(6):
            test_photo(source / f"frame_{i}.jpg", 300+i)
        bank.import_media(source, target=600, confirm_rights=True, min_distance=0)
        all_rows = bank.templates("general", limit=6)
        self.assertEqual(len(all_rows), 6)
        first_three = {x["id"] for x in all_rows[:3]}
        next_batch = bank.templates("general", seen=first_three, limit=6)
        self.assertTrue(next_batch)
        self.assertTrue(all(t["id"] not in first_three for t in next_batch))
        self.assertEqual(len(next_batch), 3)

    def test_creates_admin_review_sheets(self):
        source = self.root / "video" / "shock"
        for i in range(4):
            test_photo(source / f"still_{i}.jpg", 800+i)
        bank.import_media(source, target=600, confirm_rights=True, min_distance=0)
        review=bank.contact_sheets(per_sheet=60)
        self.assertEqual(len(review), 1)
        with Image.open(review[0]) as sheet:
            self.assertEqual(sheet.size, (1500, 750))

    def test_real_frame_only_no_drawing_or_remote_fetch(self):
        self.assertNotIn("image_gen", bank.__doc__)
        self.assertFalse(hasattr(bank, "draw_face"))
        self.assertFalse(hasattr(bank, "download_scraped_media"))
        self.assertEqual(bank.status()["count"], 0)

    def test_originals_never_checked_into_git(self):
        gitignore=(Path(__file__).resolve().parents[1] / ".gitignore").read_text()
        self.assertIn("data/", gitignore)


if __name__ == "__main__":
    unittest.main()
