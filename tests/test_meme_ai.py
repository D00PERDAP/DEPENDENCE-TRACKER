"""Optional paid AI meme writer tests run offline; no API requests or tokens."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meme_studio import Joke
import meme_ai as ai


class AIMemeTests(unittest.TestCase):
    def test_local_safety_and_originality_gate(self):
        joke = Joke("general", "МЕФ: ПОСЛЕДСТВИЯ", "Когда обещал завтра всё исправить", "А завтра подало на меня в суд")
        self.assertTrue(ai.acceptable(joke))
        self.assertTrue(ai.too_similar(joke, [joke]))
        self.assertFalse(ai.acceptable(Joke("general", "ИНСТРУКЦИЯ", "Как приготовить наркотик дома", "Никогда не делай этого")))
        self.assertFalse(ai.acceptable(Joke("missing", "ПОДЗАГОЛОВОК", "Как-то было вчера очень странно", "А сегодня всё стало ещё страннее")))
        self.assertFalse(ai.acceptable(Joke("general", "КРАТКО", "Да", "Нет")))

    def test_structured_response_parsing(self):
        joke = {"category": "general", "label": "ТРЕВОЖНЫЙ ВЕЧЕР",
                "setup": "Завтра точно начну новую жизнь", "punchline": "Старая уже оформила продление подписки"}
        payload = {"status": "completed", "output": [
            {"type": "message", "content": [
                {"type": "output_text", "text": json.dumps({"memes": [joke]}, ensure_ascii=False)}]}]}
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return None
            def read(self, *args):
                return json.dumps(payload).encode("utf-8")
        with patch("meme_ai.urllib.request.urlopen", return_value=FakeResponse()) as open_mock:
            result = ai.ask_model("Пожалуйста напиши шутки", key="FAKE_TOKEN")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].category, "general")
        self.assertEqual(result[0].label, "ТРЕВОЖНЫЙ ВЕЧЕР")
        sent = json.loads(open_mock.call_args.args[0].data)
        self.assertEqual(sent["text"]["format"]["type"], "json_schema")
        self.assertNotIn("FAKE_TOKEN", json.dumps(sent))

    def test_daily_cost_guard_and_offline_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            index = Path(tmp) / "ai_original_memes.json"
            joke = Joke("general", "ТРИЛЛЕР В КАРМАНЕ",
                        "Открыл банковское приложение утром",
                        "Там оказался финал фильма ужасов")
            output = Path(tmp) / "meme.jpg"
            with patch.dict("os.environ", {"MEME_AI_AUTOGEN": "1", "OPENAI_API_KEY": "FAKE_TOKEN"}):
                with patch.object(ai, "STATE", state), patch.object(ai, "INDEX", index):
                    with patch.object(ai, "ask_model", side_effect=[[joke], [joke]]) as mock_model:
                        with patch.object(ai, "meme_path", return_value=output):
                            with patch.object(ai, "render", side_effect=lambda j, p: Path(p).write_bytes(b"image")):
                                self.assertEqual(ai.generate_daily(), 1)
                                self.assertEqual(ai.generate_daily(), 0)
                    self.assertEqual(mock_model.call_count, 2)
                    self.assertEqual(len(json.loads(index.read_text())), 1)


if __name__ == "__main__":
    unittest.main()
