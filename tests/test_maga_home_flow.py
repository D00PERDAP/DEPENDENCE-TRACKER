"""Guardrails against accidentally replacing the accompaniment screen with a meme."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def function(name, filename):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef) and node.name == name)


class MagaHomeFlowTests(unittest.TestCase):
    def test_home_prefers_real_photo_and_has_line_breaks(self):
        node = function("home", "bot.py")
        code = ast.get_source_segment(
            (ROOT / "bot.py").read_text(encoding="utf-8"), node
        )
        self.assertIn('assets" / "maga_menu_photo.jpg"', code)
        self.assertIn("ui_manager.render_photo", code)
        self.assertNotIn("render_rich(", code)
        # A source literal with double-escaped newline renders as visible backslash-n.
        self.assertNotIn(chr(92) * 2 + "n", code)

    def test_frequency_handler_does_not_replace_confirmation_with_meme(self):
        source = (ROOT / "v2_experience.py").read_text(encoding="utf-8")
        start = source.index('if action in ("freq", "setfreq"):')
        stop = source.index('if action == "activate10":', start)
        frequency_handler = source[start:stop]
        self.assertIn("СОПРОВОЖДЕНИЕ ВКЛЮЧЕНО", frequency_handler)
        self.assertNotIn("real_memes.send(", frequency_handler)


if __name__ == "__main__":
    unittest.main()
