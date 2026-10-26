"""Expired reminder messages do not accumulate or replace the home menu."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import reminder_messages


class FakeBot:
    def __init__(self):
        self.deleted = []

    async def delete_message(self, user_id, message_id):
        self.deleted.append((user_id, message_id))


class ReminderLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = reminder_messages.DB
        reminder_messages.DB = Path(self.temp.name) / "temporary.sqlite3"
        self.bot = FakeBot()

    def tearDown(self):
        reminder_messages.DB = self.old_db
        self.temp.cleanup()

    async def test_expires_and_is_deleted(self):
        start = datetime(2026, 10, 26, tzinfo=timezone.utc)
        reminder_messages.remember(123, 7, now=start)
        await reminder_messages.cleanup_expired(self.bot, start + timedelta(seconds=89))
        self.assertEqual(self.bot.deleted, [])
        await reminder_messages.cleanup_expired(self.bot, start + timedelta(seconds=91))
        self.assertEqual(self.bot.deleted, [(123, 7)])
        self.assertIsNone(reminder_messages.previous(123))

    async def test_new_reminder_removes_previous(self):
        reminder_messages.remember(123, 7)
        await reminder_messages.replace_existing(self.bot, 123)
        reminder_messages.remember(123, 8)
        self.assertEqual(self.bot.deleted, [(123, 7)])
        self.assertEqual(reminder_messages.previous(123)["message_id"], 8)

    async def test_users_are_isolated(self):
        reminder_messages.remember(123, 7)
        reminder_messages.remember(456, 9)
        await reminder_messages.replace_existing(self.bot, 123)
        self.assertEqual(self.bot.deleted, [(123, 7)])
        self.assertEqual(reminder_messages.previous(456)["message_id"], 9)

    def test_reminders_do_not_modify_home_screen(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
        self.assertIn("reminder_messages.cleanup_expired(bot, current)", source)
        self.assertIn("reminder_messages.remember(uid, sent.message_id)", source)
        self.assertIn("reminder_messages.replace_existing(bot, uid)", source)


if __name__ == "__main__":
    unittest.main()
