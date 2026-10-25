"""Dynamic sea-creature meme engine: no repeats, JPEGs, feedback, concurrency."""
from __future__ import annotations

import asyncio
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

import bot
import meme_engine


class DynamicMemeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch.object(bot, "DB", Path(self.tmp.name) / "tracker.db")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        bot.init()

    def test_unique_serials_survive_reconnect_and_no_old_users_modified(self):
        user = 4569
        serials = [meme_engine.next_serial(bot.connect, user) for _ in range(50)]
        self.assertEqual(serials, list(range(1, 51)))
        self.assertEqual(meme_engine.next_serial(bot.connect, user), 51)
        self.assertEqual(meme_engine.next_serial(bot.connect, 789), 1)
        with bot.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_unique_images_even_with_identical_seed_and_context(self):
        rendered = []
        keys = set()
        for serial in range(1, 13):
            spec = meme_engine.make_spec("Мефедрон", "ex", 777, serial, seed=1011)
            self.assertEqual(spec.risk, "ex")
            self.assertNotIn(spec.key, keys)
            keys.add(spec.key)
            payload = meme_engine.render(spec)
            with Image.open(io.BytesIO(payload)) as im:
                self.assertEqual(im.format, "JPEG")
                self.assertEqual(im.size, (850, 850))
                self.assertGreater(len(payload), 9000)
            rendered.append(payload)
        self.assertEqual(len(set(rendered)), len(rendered))

    def test_context_and_original_sea_archetypes(self):
        self.assertGreaterEqual(len(meme_engine.ARCHETYPES), 8)
        self.assertGreaterEqual(len(meme_engine.PLACES), 10)
        for risk in ("ex", "more", "money", "sleep", "travel", "paranoia"):
            s = meme_engine.make_spec("Мефедрон", risk, 99, 2, seed=22)
            self.assertEqual(s.risk, risk)
            self.assertTrue(s.top and s.bottom)
        unknown = meme_engine.make_spec("Другое", "unknown", 99, 3, seed=22)
        self.assertEqual(unknown.risk, "general")


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch.object(bot, "DB", Path(self.tmp.name) / "tracker.db")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        bot.init()

    async def test_each_click_sends_new_jpeg_with_unique_vote_key(self):
        msg = type("FakeMessage", (), {"answer_photo": AsyncMock()})()
        for _ in range(3):
            self.assertTrue(await meme_engine.send(msg, 12345, "Мефедрон",
                                                    "ex", bot.connect))
        self.assertEqual(msg.answer_photo.await_count, 3)
        keys = []
        for call in msg.answer_photo.await_args_list:
            data = call.args[0].data
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.format, "JPEG")
            keyboard = call.kwargs["reply_markup"]
            yes = keyboard.inline_keyboard[0][0].callback_data
            self.assertTrue(yes.startswith("mv:1:"))
            keys.append(yes[5:])
        self.assertEqual(len(set(keys)), 3)
        with bot.connect() as c:
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM meme_dynamic_history WHERE user_id=?",
                (12345,)).fetchone()[0], 3)
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM meme_sends WHERE user_id=?",
                (12345,)).fetchone()[0], 3)

    async def test_failure_does_not_record_as_sent(self):
        msg = type("FakeMessage", (), {"answer_photo": AsyncMock(
            side_effect=RuntimeError("telegram temporarily offline"))})()
        with self.assertRaises(RuntimeError):
            await meme_engine.send(msg, 1700, "MDMA", "money", bot.connect)
        with bot.connect() as c:
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM meme_sends WHERE user_id=1700").fetchone()[0], 0)
            self.assertEqual(c.execute(
                "SELECT serial FROM meme_dynamic_counter WHERE user_id=1700").fetchone()[0], 1)

    async def test_pure_in_memory_reminder_does_not_need_assets_or_network(self):
        fake = type("FakeBot", (), {"send_photo": AsyncMock()})()
        with patch.object(meme_engine, "render", wraps=meme_engine.render) as mocked:
            await meme_engine.send_bot(fake, 177, "Мефедрон", "more", bot.connect,
                                       "Не звони бывшей", None)
            await meme_engine.send_bot(fake, 177, "Мефедрон", "more", bot.connect,
                                       "Не звони бывшей", None)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(fake.send_photo.await_count, 2)
        photos = [item.args[1].data for item in fake.send_photo.await_args_list]
        self.assertNotEqual(photos[0], photos[1])
        with bot.connect() as c:
            rows = c.execute(
                "SELECT meme_key FROM meme_dynamic_history WHERE user_id=177"
                " ORDER BY serial").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0][0], rows[1][0])


if __name__ == "__main__":
    unittest.main()
