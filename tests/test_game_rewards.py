"""Regression tests for RP geocaching awards, coordinates and player privacy."""
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

    def test_gps_pair_and_ranges(self):
        self.assertEqual(
            game_rewards.parse_coordinates("55.751244, 37.618423"),
            (55.751244, 37.618423),
        )
        self.assertEqual(
            game_rewards.parse_coordinates("-12.5; 42.24"),
            (-12.5, 42.24),
        )
        for value in ("999, 30", "30, 200", "foo", "20, 30, 40"):
            with self.assertRaises(ValueError):
                game_rewards.parse_coordinates(value)

    def test_real_gps_is_saved_per_player(self):
        rid = game_rewards.grant(111, 22, "Сундук", latitude=55.751244, longitude=37.618423)
        row = game_rewards.inventory(111)[0]
        self.assertEqual(row["id"], rid)
        self.assertAlmostEqual(row["latitude"], 55.751244)
        self.assertAlmostEqual(row["longitude"], 37.618423)
        self.assertEqual(game_rewards.inventory(222), [])

    def test_ack_ownership_check_and_persistent_inventory(self):
        source = Path(game_rewards.__file__).read_text(encoding="utf-8")
        self.assertIn('row["user_id"] != q.from_user.id', source)
        self.assertIn('"SELECT * FROM rewards WHERE id=? AND user_id=?"', source)
        self.assertIn("q.message.delete()", source)
        self.assertIn("RewardFlow.location", source)



if __name__ == "__main__":
    unittest.main()
