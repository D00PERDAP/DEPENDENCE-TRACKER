"""Regression tests for fictional game rewards without private location disclosure."""
import tempfile
import unittest
from pathlib import Path

import game_rewards


class RewardTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.old_db = game_rewards.DB
        game_rewards.DB = Path(self.folder.name) / "rewards.sqlite3"

    def tearDown(self):
        game_rewards.DB = self.old_db
        self.folder.cleanup()

    def test_grant_persists_after_reopening_and_is_user_scoped(self):
        rid = game_rewards.grant(111, 22, "Сундук Маги")
        self.assertEqual(game_rewards.inventory(111)[0]["id"], rid)
        self.assertEqual(game_rewards.inventory(111)[0]["title"], "Сундук Маги")
        self.assertEqual(game_rewards.inventory(222), [])
        with game_rewards.connect() as c:
            row = c.execute("SELECT * FROM rewards WHERE id=?", (rid,)).fetchone()
        self.assertIsNone(row["acknowledged_at"])
        self.assertIsNone(row["notice_message_id"])

    def test_reject_blank_or_long_titles(self):
        for title in ("", "  ", "A" * 81):
            with self.assertRaises(ValueError):
                game_rewards.grant(111, 22, title)

    def test_reward_code_has_no_geographic_data_or_location_sends(self):
        source = Path(game_rewards.__file__).read_text(encoding="utf-8")
        self.assertNotIn("answer_location", source)
        self.assertNotIn("send_location", source)
        self.assertNotIn("latitude", source)
        self.assertNotIn("longitude", source)
        self.assertIn("q.message.delete()", source)
        self.assertIn('row["user_id"] != q.from_user.id', source)


if __name__ == "__main__":
    unittest.main()
