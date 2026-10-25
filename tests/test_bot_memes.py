"""Bot meme integration regressions. Uses only a temporary SQLite database and files."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot


class BotMemeTests(unittest.TestCase):
    def test_user_history_and_quality_suppression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "assets" / "memes" / "general"
            folder.mkdir(parents=True)
            (folder / "a.jpg").write_bytes(b"fake-a")
            (folder / "b.jpg").write_bytes(b"fake-b")
            with patch.object(bot, "__file__", str(root / "bot.py")), patch.object(bot, "DB", root / "tracker.db"):
                bot.init()
                with patch.object(bot.random, "choice", side_effect=lambda seq: seq[0]):
                    first = bot.choose_meme("general", user_id=123)
                    self.assertIsNotNone(first)
                    bot.record_meme(123, first)
                    second = bot.choose_meme("general", user_id=123)
                    self.assertNotEqual(first, second)
                    key = bot.meme_key(first)
                    with bot.connect() as c:
                        for user in range(1000, 1004):
                            c.execute("INSERT INTO meme_votes(user_id,meme_key,vote) VALUES(?,?,?)",
                                      (user, key, -1))
                    self.assertNotEqual(first, bot.choose_meme("general", user_id=300))
                    with bot.connect() as c:
                        rows = c.execute("SELECT meme_key FROM meme_sends WHERE user_id=123").fetchall()
                        self.assertEqual([row[0] for row in rows], [key])

    def test_schema_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(bot, "DB", Path(tmp) / "tracker.db"):
                bot.init()
                bot.init()
                with bot.connect() as c:
                    tables = {x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("meme_sends", tables)
                self.assertIn("meme_votes", tables)


if __name__ == "__main__":
    unittest.main()
