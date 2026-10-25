"""Regression guards for the consolidated Maga screens."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MagaPolishTests(unittest.TestCase):
    def test_admin_and_settings_have_real_newlines(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertNotIn(chr(92) * 2 + "n", source)
        for callback in ("admin:memehub", "ui:mine", "ui:privacy"):
            self.assertIn(callback, source)
        self.assertIn('("😂 Мемный центр", "admin:memehub")', source)

    def test_help_substance_navigation(self):
        flow = (ROOT / "v2_experience.py").read_text(encoding="utf-8")
        for action in ("prepchoose", "prep", "selfhelp", "recovery", "emergency"):
            self.assertIn('if action == "' + action + '":', flow)
        self.assertIn("safety.preparation_card(substance)", flow)

    def test_short_substance_risk_cards_and_red_flags(self):
        import harm_reduction
        for substance in harm_reduction.SUBSTANCES:
            card = harm_reduction.preparation_card(substance)
            self.assertIn(substance.upper(), card)
            self.assertIn("безопасный", card)
            self.assertIn("Боль в груди", card)

    def test_photo_and_geo_remain_separate(self):
        quest = (ROOT / "pirate_quests.py").read_text(encoding="utf-8")
        self.assertIn("Твои корды, вацок", quest)
        self.assertIn("Твоя фотка", quest)
        self.assertIn("answer_location(", quest)
        self.assertIn("answer_photo(", quest)


if __name__ == "__main__":
    unittest.main()
