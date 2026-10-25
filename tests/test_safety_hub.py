"""Safety hub navigation should stay available without changing personal records."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = (ROOT / "v2_experience.py").read_text(encoding="utf-8")


class SafetyHubTests(unittest.TestCase):
    def test_all_hub_actions_exist(self):
        for action in ("safety", "selfhelp", "nightprep", "recovery", "emergency"):
            self.assertIn(f'if action == "{action}":', FLOW)

    def test_entry_has_emergency_and_self_help(self):
        hub = FLOW.split('if action == "safety":', 1)[1].split('if action == "selfhelp":', 1)[0]
        for callback in ("v2:emergency", "v2:selfhelp", "v2:nightprep",
                         "v2:recovery", "v2:questions"):
            self.assertIn(callback, hub)

    def test_emergency_does_not_claim_to_auto_dial(self):
        emergency = FLOW.split('if action == "emergency":', 1)[1].split('if action == "memethemes":', 1)[0]
        self.assertIn("112", emergency)
        self.assertIn("Телефон", emergency)
        self.assertIn("диспетчер", emergency)

    def test_nightprep_recommends_stopping(self):
        screen = FLOW.split('if action == "nightprep":', 1)[1].split('if action == "recovery":', 1)[0]
        self.assertIn("не начинать", screen)
        self.assertIn("не смешивай", screen.casefold())


if __name__ == "__main__":
    unittest.main()
