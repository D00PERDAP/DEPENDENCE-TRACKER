"""Countdown should not create extra messages or change the deletion deadline."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "pirate_quests.py").read_text(encoding="utf-8")


class ExpiryCountdownTests(unittest.TestCase):
    def test_arrival_countdown_is_one_minute(self):
        start = SOURCE.index('async def arrive(')
        fragment = SOURCE[start:]
        tree = ast.parse(fragment)
        delays = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Tuple) and all(
                isinstance(x, ast.Constant) and isinstance(x.value, int)
                for x in node.elts
            ):
                values = [x.value for x in node.elts]
                if values == [15, 15, 15, 5, 5, 5]:
                    delays = values
        self.assertEqual(sum(delays), 60)

    def test_countdown_edits_caption_only(self):
        section = SOURCE[SOURCE.index('async def arrive('):]
        self.assertIn('edit_caption(', section)
        self.assertIn('До удаления:', section)
        self.assertNotIn('answer_photo(', section)
        self.assertNotIn('answer_location(', section)


if __name__ == "__main__":
    unittest.main()
