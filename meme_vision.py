"""Opt-in paid vision prefilter for downloaded third-party meme candidates.

Analysis is never evidence of redistribution rights. Images remain in admin review.
Default OFF; no user data transmitted, only already-public meme thumbnails.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

from meme_ai import API_URL

ROOT = Path(__file__).resolve().parent
REVIEW = (ROOT / "data" / "meme_review").resolve()
SCHEMA = {
    "type": "object",
    "properties": {
        "on_topic": {"type": "boolean"},
        "humor": {"type": "integer"},
        "readable": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["on_topic", "humor", "readable", "reason"],
    "additionalProperties": False,
}


def enabled():
    return os.getenv("MEME_AI_VISION", "0") == "1" and bool(os.getenv("OPENAI_API_KEY"))


def analyze(path):
    """Return analysis dict or None when disabled. Does not approve or publish."""
    if not enabled():
        return None
    path = Path(path).resolve()
    if not path.is_file() or not path.is_relative_to(REVIEW):
        raise ValueError("Preview outside controlled review directory")
    data = path.read_bytes()
    if len(data) > 1_200_000:
        raise ValueError("Vision thumbnail too large")
    payload = {
        "model": os.getenv("MEME_AI_MODEL", "gpt-4o-mini"),
        "instructions": (
            "Оцени картинку как русскоязычный мем для взрослой аудитории, любящей "
            "самоиронию и тёмный юмор о последствиях употребления наркотиков, ночных "
            "звонках, деньгах, паранойе и трезвости. Не романтизируй употребление, "
            "не определяй личности людей по лицам. Оцени именно видимый смысл, "
            "а не URL. on_topic = явно ли мем связан с темой; humor = 0..5 насколько "
            "понятна и неожиданна шутка; readable = можно ли разобрать смысл/надпись. "
            "Это НЕ проверка авторских прав и НЕ решение о публикации. Верни JSON."
        ),
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Нужна строгая предварительная проверка мема."},
                {"type": "input_image", "image_url":
                    "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"),
                    "detail": "high"},
            ],
        }],
        "text": {"format": {"type": "json_schema", "name": "meme_vision_score",
                             "strict": True, "schema": SCHEMA}},
        "max_output_tokens": 350,
    }
    req = urllib.request.Request(API_URL, method="POST", data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as response:
        body = response.read(50_001)
    if len(body) > 50_000:
        raise ValueError("Vision result too large")
    response = json.loads(body)
    if response.get("status") != "completed":
        raise ValueError("Incomplete vision analysis")
    texts = [part.get("text", "") for message in response.get("output", [])
             if message.get("type") == "message"
             for part in message.get("content", [])
             if part.get("type") == "output_text"]
    if not texts:
        raise ValueError("No vision output")
    score = json.loads("".join(texts))
    if not (isinstance(score.get("on_topic"), bool) and
            isinstance(score.get("readable"), bool) and
            type(score.get("humor")) is int and 0 <= score["humor"] <= 5):
        raise ValueError("Invalid vision rating")
    return score
