"""Optional regional-voice selector must not break normal free Edge TTS."""
import tempfile
import unittest
from pathlib import Path

import voice_broadcast


class RegionalVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = voice_broadcast.VOICE_DB
        voice_broadcast.VOICE_DB = Path(self.temp.name) / "voice.sqlite3"

    def tearDown(self):
        voice_broadcast.VOICE_DB = self.original
        self.temp.cleanup()

    def test_edge_is_default(self):
        self.assertEqual(voice_broadcast.get_voice(123), "edge")

    def test_voice_choice_persists(self):
        voice_broadcast.set_voice(123, "gamat")
        voice_broadcast.set_voice(456, "eduard")
        self.assertEqual(voice_broadcast.get_voice(123), "gamat")
        self.assertEqual(voice_broadcast.get_voice(456), "eduard")
        voice_broadcast.set_voice(123, "edge")
        self.assertEqual(voice_broadcast.get_voice(123), "edge")

    def test_invalid_option_is_rejected(self):
        with self.assertRaises(ValueError):
            voice_broadcast.set_voice(123, "some-user-controlled-shell-code")

    def test_local_worker_is_optional(self):
        source = Path(voice_broadcast.__file__).read_text(encoding="utf-8")
        worker = Path(__file__).resolve().parents[1] / "silero_local_voice.py"
        self.assertTrue(worker.exists())
        self.assertIn('if selected_voice == "edge":', source)
        self.assertIn("synthesize_silero(body, path, selected_voice)", source)
        self.assertIn("ru_gamat", worker.read_text(encoding="utf-8"))
        self.assertIn("ru_eduard", worker.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
