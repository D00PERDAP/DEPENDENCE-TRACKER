"""Admin voice message TTS regression guards."""
import unittest
from pathlib import Path
from unittest.mock import patch

import voice_broadcast


class VoiceBroadcastTests(unittest.TestCase):
    def test_voice_config_and_output_is_ogg(self):
        self.assertEqual(voice_broadcast.VOICE_NAME, "ru-RU-DmitryNeural")
        self.assertLessEqual(voice_broadcast.MAX_CHARS, 700)
        source = Path(voice_broadcast.__file__).read_text(encoding="utf-8")
        self.assertIn('"-c:a", "libopus"', source)
        self.assertIn('FSInputFile(path)', source)
        self.assertIn("await m.bot.send_voice", source)
        self.assertIn('q.bot.send_voice(', source)

    def test_broadcast_requires_preview_and_admin(self):
        source = Path(voice_broadcast.__file__).read_text(encoding="utf-8")
        self.assertIn('is_admin(q.from_user.id)', source)
        self.assertIn('VoiceFlow.confirm.state', source)
        self.assertIn('await state.clear()', source)
        self.assertIn('maga_voice_preview_id', source)
        self.assertIn('voice_broadcast', source)

    def test_menu_is_present_and_deps_declared(self):
        root = Path(voice_broadcast.__file__).resolve().parent
        self.assertIn('("🎙 Голос Маги", "ac:voice")',
                      (root / "bot.py").read_text(encoding="utf-8"))
        self.assertIn("edge-tts", (root / "requirements.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
