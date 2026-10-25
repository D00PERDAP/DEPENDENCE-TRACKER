"""Maga voice remains consistent without changing user actions."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MagaStyleTests(unittest.TestCase):
    def test_settings_routes_and_voice(self):
        src = (ROOT / "bot.py").read_text(encoding="utf-8")
        for label in ("🦅 МАГА | ТВОИ НАСТРОЙКИ", "🔐 МАГА | ЛИЧНОЕ ДЕЛО",
                      "🦅 МАГА | ШТАБ"):
            self.assertIn(label, src)
        for route in ("ui:mine", "ui:privacy", "v2:settings",
                      "v2:stats", "delete_ask", "v2:home"):
            self.assertIn(route, src)
        self.assertNotIn(chr(92) * 2 + "n", src)

    def test_reminders_stay_functional(self):
        src = (ROOT / "v2_experience.py").read_text(encoding="utf-8")
        self.assertIn("🦅 МАГА | НАПОМИНАЛКИ", src)
        for action in ("v2:setfreq:10", "v2:setfreq:15",
                       "v2:setfreq:30", "v2:setfreq:60",
                       "v2:quiet", "v2:boost"):
            self.assertIn(action, src)

    def test_admin_analytics_not_personal_content(self):
        src = (ROOT / "admin_center.py").read_text(encoding="utf-8")
        self.assertIn("🦅 МАГА | РАСКЛАДЫ ШТАБА", src)
        self.assertIn("Личные записи не показываем", src)


if __name__ == "__main__":
    unittest.main()
