"""Optional AI writer + editor for *original* Russian dark-humour memes.

Off by default, because OPENAI_API_KEY uses a separately billed API.
No personal user information is ever sent; only generic joke-writing prompts.
External copyrighted images are never sent to this API or republished here.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from meme_studio import Joke, catalog, meme_path, render
from meme_review_store import read, transact

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "data" / "ai_original_memes.json"
STATE = ROOT / "data" / "ai_meme_updates.json"
API_URL = "https://api.openai.com/v1/responses"
MODEL = "gpt-4o-mini"
CATEGORIES = ("general", "ex", "calls", "money", "travel")
FORBIDDEN = re.compile(
    r"как (сварить|сделать|приготовить|употреблять|принять|спрятать|продать|купить)"
    r"|где (купить|достать)|инструкци[яию]|рецепт наркот|дозировк|"
    r"несовершеннолетн|педофил|изнасилован|суицид", re.IGNORECASE,
)
SCHEMA = {
    "type": "object",
    "properties": {
        "memes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": list(CATEGORIES)},
                "label": {"type": "string"},
                "setup": {"type": "string"},
                "punchline": {"type": "string"},
            },
            "required": ["category", "label", "setup", "punchline"],
            "additionalProperties": False,
        }}
    },
    "required": ["memes"],
    "additionalProperties": False,
}


def acceptable(joke):
    if joke.category not in CATEGORIES:
        return False
    if not all(isinstance(x, str) and x.strip() for x in
               (joke.label, joke.setup, joke.punchline)):
        return False
    if not (5 <= len(joke.label) <= 48 and 15 <= len(joke.setup) <= 110
            and 20 <= len(joke.punchline) <= 120):
        return False
    text = " ".join((joke.label, joke.setup, joke.punchline))
    if FORBIDDEN.search(text):
        return False
    return joke.setup.lower().strip() != joke.punchline.lower().strip()


def too_similar(joke, existing, threshold=0.77):
    candidate = (joke.setup + " " + joke.punchline).casefold()
    for other in existing:
        original = (other.setup + " " + other.punchline).casefold()
        if SequenceMatcher(None, candidate, original).ratio() >= threshold:
            return True
    return False


def ask_model(prompt, key=None):
    """Use documented Responses Structured Outputs with a strict JSON schema."""
    key = key or os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("OPENAI_API_KEY is required")
    payload = {
        "model": os.getenv("MEME_AI_MODEL", MODEL),
        "instructions": (
            "Ты русскоязычный автор оригинальных мемов с острым, иногда матерным и "
            "мрачным юмором про мефедрон, соли, Пашу Техника, финансовые последствия "
            "тусовок, бывших, звонки ночью и беспокойную голову. Смеёмся над "
            "абсурдом и плохими решениями, НЕ романтизируем наркотики. "
            "Не копируй известные мемы, цитаты, песни или чужие шутки; не приписывай "
            "настоящим людям выдуманные цитаты, не выдавай инструкций по веществам. "
            "Требуется яркая неожиданная вторая строка (punchline), не банальные "
            "мотивирующие фразы. Русский язык. Возвращай только заданный JSON."
        ),
        "input": prompt,
        "text": {"format": {"type": "json_schema", "name": "original_dark_memes",
                             "strict": True, "schema": SCHEMA}},
        "max_output_tokens": 2800,
    }
    request = urllib.request.Request(API_URL, data=json.dumps(payload).encode("utf-8"),
                                     method="POST", headers={
                                         "Authorization": "Bearer " + key,
                                         "Content-Type": "application/json",
                                         "User-Agent": "DependenceTrackerOriginalMemes/1.0",
                                     })
    with urllib.request.urlopen(request, timeout=35) as response:
        body = response.read(350_001)
    if len(body) > 350_000:
        raise ValueError("AI response too large")
    response = json.loads(body)
    if response.get("status") != "completed":
        raise ValueError("AI response was not completed")
    texts = [block.get("text", "") for output in response.get("output", [])
             if output.get("type") == "message"
             for block in output.get("content", [])
             if block.get("type") == "output_text"]
    if not texts:
        raise ValueError("AI produced no structured text")
    parsed = json.loads("".join(texts))
    return [Joke(**x) for x in parsed["memes"]]


def restore():
    """Regenerate local files for previously accepted AI originals after restart."""
    files = []
    for entry in read(INDEX)[-1200:]:
        joke = Joke(**{k: entry[k] for k in ("category", "label", "setup", "punchline")})
        if acceptable(joke):
            path = meme_path(joke)
            if not path.is_file() or path.stat().st_size == 0:
                render(joke, path)
            files.append(path)
    return files


def generate_daily():
    """Add up to six original jokes daily if explicitly enabled and key exists."""
    if os.getenv("MEME_AI_AUTOGEN", "0") != "1" or not os.getenv("OPENAI_API_KEY"):
        return 0
    now = datetime.now(timezone.utc)
    runs = read(STATE)
    if runs:
        last = datetime.fromisoformat(runs[-1]["time"])
        if now - last < timedelta(hours=23):
            return 0
    # Record the attempt *before* paid API calls to avoid expensive restart loops.
    transact(lambda rows: rows.append({"time": now.isoformat(), "result": "attempt"}), STATE)
    prior = [Joke(**{k: entry[k] for k in ("category", "label", "setup", "punchline")})
             for entry in read(INDEX)]
    existing = list(catalog()) + prior
    themes = "меф, Паша Техник, соли, наркоманские мемы, ночная паранойя, деньги, бывшая, сленг"
    candidates = ask_model(
        "Создай 12 совершенно новых русских двустрочных мемов с чёрным юмором. "
        "Разнообразь темы: " + themes + ". "
        "Каждый мем должен быть понятным с первого взгляда: setup задаёт ожидание, "
        "punchline резко переворачивает его; без шаблонов «я: / мозг:» более двух раз."
    )
    candidates = [j for j in candidates if acceptable(j) and not too_similar(j, existing)]
    if not candidates:
        return 0
    # Independent editorial pass. Do not treat model scores as objective truth.
    approved = ask_model(
        "Ты строгий редактор чёрного юмора. Оцени эти новые идеи. Верни только "
        "4-6 реально смешных, коротких и понятных шуток. Исправляй слабые концовки "
        "оригинальными неожиданными панчлайнами. Отсекай скучные, повторяющиеся "
        "или романтизирующие вещества варианты:\n" +
        json.dumps([j.__dict__ for j in candidates[:12]], ensure_ascii=False)
    )
    accepted = []
    for joke in approved:
        if not acceptable(joke) or too_similar(joke, existing + accepted):
            continue
        try:
            path = meme_path(joke)
            if not path.is_file():
                render(joke, path)
            accepted.append(joke)
        except (OSError, ValueError, RuntimeError):
            continue
        if len(accepted) >= 6:
            break
    if not accepted:
        return 0
    def save(rows):
        seen = {(x["setup"], x["punchline"]) for x in rows}
        for j in accepted:
            if (j.setup, j.punchline) in seen:
                continue
            rows.append({**j.__dict__, "created_at": now.isoformat(),
                         "provenance": "original_ai_generated"})
        return len(accepted)
    return transact(save, INDEX)


if __name__ == "__main__":
    print("AI generation enabled:", os.getenv("MEME_AI_AUTOGEN", "0") == "1")
    print("Restored originals:", len(restore()))
    print("New originals:", generate_daily())
