"""Offline picture-meme and real 10-minute delivery regression tests."""
from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

import bot
import context_memes
import photo_memes
import v2_data


class PictureMemeTests(unittest.TestCase):
    def test_offline_pack_is_real_jpeg_art_not_text_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = photo_memes.ensure_pack(Path(tmp), limit=4)
            self.assertEqual(len(files), 4)
            self.assertTrue(all(p.is_file() and p.stat().st_size > 1000 for p in files))
            for path in files:
                with Image.open(path) as image:
                    self.assertEqual(image.size, (850, 850))
                    self.assertEqual(image.format, "JPEG")
            self.assertEqual(photo_memes.ensure_pack(Path(tmp), limit=4), files)

    def test_context_tags_match_actual_generated_picture_memes(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo_memes.ensure_pack(Path(tmp))
            self.assertTrue(context_memes.candidates("Мефедрон", "more", Path(tmp)))
            self.assertTrue(context_memes.candidates("Мефедрон", "ex", Path(tmp)))
            self.assertTrue(context_memes.candidates("Альфа-ПВП", "paranoia", Path(tmp)))
            self.assertTrue(context_memes.candidates("Каннабис", "check", Path(tmp)))
            self.assertTrue(context_memes.candidates("Другое / не знаю", "money", Path(tmp)))
            self.assertTrue(all("original_" not in p.name
                                for p in context_memes.candidates("Мефедрон", "ex", Path(tmp))))

    def test_stock_photos_optional_no_network_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MEME_STOCK_PHOTOS": "0"}):
                with patch.object(photo_memes.urllib.request, "urlopen") as call:
                    result = photo_memes.upgrade_photos(Path(tmp), Path(tmp) / "cache")
            self.assertEqual(result, 0)
            call.assert_not_called()


class TelegramMemePhotoTests(unittest.IsolatedAsyncioTestCase):
    async def test_meme_button_sends_photo_not_a_text_joke(self):
        with tempfile.TemporaryDirectory() as tmp:
            picture = Path(tmp) / "approved.jpg"
            Image.new("RGB", (640, 640), "#333333").save(picture)
            msg = type("FakeMessage", (), {
                "answer_photo": AsyncMock(), "answer": AsyncMock()
            })()
            with patch.object(context_memes, "candidates", return_value=[picture]):
                with patch.object(context_memes, "choose_photo", return_value=picture):
                    seen = []
                    result = await context_memes.send(
                        msg, 42, "Мефедрон", "more",
                        bot.connect, lambda _: "a" * 16,
                        lambda uid, p: seen.append((uid, p)),
                        lambda p: None,
                    )
            self.assertTrue(result)
            msg.answer_photo.assert_awaited_once()
            msg.answer.assert_not_awaited()
            self.assertEqual(seen, [(42, picture)])

    async def test_missing_stored_photo_retries_in_memory_not_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            picture = Path(tmp) / "gone.jpg"
            # Bad/stale FSInputFile: first Telegram delivery raises, then the
            # in-memory JPEG can be uploaded independently.
            msg = type("FakeMessage", (), {
                "answer_photo": AsyncMock(side_effect=[OSError("stale file"), None]),
                "answer": AsyncMock(),
            })()
            with patch.object(context_memes, "choose_photo", return_value=picture):
                result = await context_memes.send(
                    msg, 42, "Мефедрон", "more",
                    bot.connect, lambda _: "a" * 16,
                    lambda uid, p: None,
                    lambda p: None,
                )
            self.assertTrue(result)
            self.assertEqual(msg.answer_photo.await_count, 2)
            self.assertTrue(hasattr(msg.answer_photo.await_args.args[0], "data"))
            msg.answer.assert_not_awaited()

    async def test_telegram_outage_does_not_double_send_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            picture = Path(tmp) / "photo.jpg"
            Image.new("RGB", (500, 500), "#333333").save(picture)
            msg = type("FakeMessage", (), {
                "answer_photo": AsyncMock(side_effect=RuntimeError("Telegram outage")),
                "answer": AsyncMock(),
            })()
            with patch.object(context_memes, "choose_photo", return_value=picture):
                result = await context_memes.send(
                    msg, 42, "Мефедрон", "more",
                    bot.connect, lambda _: "a" * 16,
                    lambda uid, p: None, lambda p: None,
                )
            self.assertFalse(result)
            msg.answer_photo.assert_awaited_once()
            msg.answer.assert_awaited_once()

    async def test_unwritable_asset_directory_still_sends_jpeg(self):
        msg = type("FakeMessage", (), {
            "answer_photo": AsyncMock(), "answer": AsyncMock()
        })()
        with patch.object(context_memes, "choose_photo", return_value=None):
            with patch.object(photo_memes, "ensure_pack",
                              side_effect=PermissionError("read-only directory")):
                result = await context_memes.send(
                    msg, 42, "Мефедрон", "more",
                    bot.connect, lambda _: "a" * 16,
                    lambda uid, p: None, lambda p: None,
                )
        self.assertTrue(result)
        msg.answer_photo.assert_awaited_once()
        msg.answer.assert_not_awaited()
        payload = msg.answer_photo.await_args.args[0]
        with Image.open(io.BytesIO(payload.data)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (850, 850))

    async def test_generator_failure_is_visible_not_disguised_as_joke(self):
        msg = type("FakeMessage", (), {
            "answer_photo": AsyncMock(), "answer": AsyncMock()
        })()
        with patch.object(context_memes, "choose_photo", return_value=None):
            with patch.object(photo_memes, "ensure_pack",
                              side_effect=PermissionError("read-only directory")):
                with patch.object(photo_memes, "inline_picture",
                                  side_effect=RuntimeError("pillow missing font")):
                    result = await context_memes.send(
                        msg, 42, "Мефедрон", "more",
                        bot.connect, lambda _: "a" * 16,
                        lambda uid, p: None, lambda p: None,
                    )
        self.assertFalse(result)
        msg.answer_photo.assert_not_awaited()
        self.assertIn("Ошибка записана", msg.answer.await_args.args[0])


class ReminderScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch = patch.object(bot, "DB", Path(self.tmp.name) / "tracker.db")
        self.patch.start()
        self.addCleanup(self.patch.stop)
        bot.init()
        self.uid = 123456789
        self.t = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
        with bot.connect() as c:
            row = c.execute(
                "INSERT INTO sessions(user_id,substance,started) VALUES(?,?,?)",
                (self.uid, "Мефедрон", v2_data.stamp(self.t - timedelta(hours=36)))
            )
            self.sid = row.lastrowid

    def test_recovery_creates_missing_schedule_with_10_minutes(self):
        with bot.connect() as c:
            self.assertIn("НЕ запланированы", bot.reminder_status_text(c, self.uid))
            due = v2_data.schedule_alert(c, self.uid, self.sid, 10, self.t)
            self.assertEqual(due, v2_data.stamp(self.t + timedelta(minutes=10)))
            v2_data.schedule_alert(c, self.uid, self.sid, 10, self.t)
            row = c.execute("SELECT * FROM alerts WHERE user_id=?", (self.uid,)).fetchone()
            self.assertEqual(row["interval_min"], 10)
            self.assertEqual(row["session_id"], self.sid)
            self.assertEqual(c.execute("SELECT count(*) FROM alerts WHERE user_id=?",
                                       (self.uid,)).fetchone()[0], 1)


class TimerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch = patch.object(bot, "DB", Path(self.tmp.name) / "tracker.db")
        self.patch.start()
        self.addCleanup(self.patch.stop)
        bot.init()
        self.uid = 222334455
        self.t = datetime.now(timezone.utc)
        with bot.connect() as c:
            row = c.execute(
                "INSERT INTO sessions(user_id,substance,started) VALUES(?,?,?)",
                (self.uid, "Мефедрон", v2_data.stamp(self.t - timedelta(hours=40)))
            )
            self.sid = row.lastrowid
            v2_data.schedule_alert(c, self.uid, self.sid, 10, self.t - timedelta(minutes=12))
        self.fake_image = Path(self.tmp.name) / "test.jpg"
        Image.new("RGB", (400, 400), "#DBA07E").save(self.fake_image)
        self.bot_api = type("FakeBot", (), {"send_photo": AsyncMock()})()

    async def _single_tick(self):
        async def stop_sleep(*args, **kwargs):
            raise asyncio.CancelledError
        with patch.object(bot.context_memes, "choose_photo", return_value=self.fake_image):
            with patch.object(bot, "record_meme"):
                with patch.object(bot.asyncio, "sleep", side_effect=stop_sleep):
                    with self.assertRaises(asyncio.CancelledError):
                        await bot.reminder_loop(self.bot_api)

    async def test_due_photo_sent_even_for_40_hour_session(self):
        await self._single_tick()
        self.bot_api.send_photo.assert_awaited_once()
        args, kw = self.bot_api.send_photo.await_args
        self.assertEqual(args[0], self.uid)
        self.assertTrue(hasattr(args[1], "path"))
        self.assertIn("Мефедрон", kw["caption"])
        with bot.connect() as c:
            self.assertIsNone(c.execute("SELECT ended FROM sessions WHERE id=?",
                                        (self.sid,)).fetchone()["ended"])
            alert = c.execute("SELECT * FROM alerts WHERE user_id=?", (self.uid,)).fetchone()
            self.assertEqual(alert["interval_min"], 10)
            next_at = datetime.fromisoformat(alert["next_at"])
            self.assertGreaterEqual(next_at, self.t + timedelta(minutes=9))

    async def test_read_only_library_still_delivers_photo_reminder(self):
        async def stop_sleep(*args, **kwargs):
            raise asyncio.CancelledError

        with patch.object(bot.context_memes, "choose_photo", return_value=None):
            with patch.object(photo_memes, "ensure_pack",
                              side_effect=PermissionError("assets are read only")):
                with patch.object(bot.asyncio, "sleep", side_effect=stop_sleep):
                    with self.assertRaises(asyncio.CancelledError):
                        await bot.reminder_loop(self.bot_api)
        self.bot_api.send_photo.assert_awaited_once()
        sent = self.bot_api.send_photo.await_args.args[1]
        with Image.open(io.BytesIO(sent.data)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (850, 850))
        with bot.connect() as c:
            alert = c.execute("SELECT next_at FROM alerts WHERE user_id=?",
                              (self.uid,)).fetchone()
            self.assertGreater(datetime.fromisoformat(alert[0]), self.t)

    async def test_failure_retries_in_one_minute_not_silently_in_ten(self):
        self.bot_api.send_photo.side_effect = RuntimeError("simulated telegram outage")
        await self._single_tick()
        self.bot_api.send_photo.assert_awaited_once()
        with bot.connect() as c:
            next_at = datetime.fromisoformat(c.execute(
                "SELECT next_at FROM alerts WHERE user_id=?", (self.uid,)
            ).fetchone()["next_at"])
        self.assertLess(next_at, datetime.now(timezone.utc) + timedelta(minutes=2))
        self.assertGreater(next_at, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
