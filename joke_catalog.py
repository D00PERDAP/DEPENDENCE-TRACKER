"""Exactly 3,000 prewritten one-sentence Russian drug-absurdist photo captions.

The shipped jokes_3000.json is the source of truth; no paid text API and no
mechanical random-word insertion at delivery time. Selection prefers the user's
current risk/theme and avoids reusing a caption until all relevant ones have
been used. No branding in the captions; each has exactly one sentence.
"""
from __future__ import annotations

import json
import random
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "jokes_3000.json"
KNOWN_MOODS = {"shock","laugh","yell","smug","confused",
               "tired","suspicious","angry","sad","neutral"}
BANNED = ("шмякобздюх","не натвори делов")
FIRST_TRY = ("general", "more", "ex", "contacts", "money", "sleep",
             "paranoia", "travel", "after", "check")


@lru_cache(maxsize=1)
def bank():
    with CATALOG.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("count") != 3000:
        raise ValueError("Caption bank must contain exactly 3000 jokes")
    result = payload["items"]
    if len(result) != 3000:
        raise ValueError("Caption file does not contain 3000 jokes")
    ids = set()
    texts = set()
    for row in result:
        uid = row["id"]
        sentence = row["text"].strip()
        if uid in ids or sentence in texts:
            raise ValueError("Duplicate joke id or text")
        if len(sentence) > 125 or len(sentence) < 24:
            raise ValueError(f"Joke length unsuitable for photo: {uid}")
        if not sentence.endswith(".") or sentence.count(".") != 1:
            raise ValueError(f"Joke is not exactly one sentence: {uid}")
        if row["risk"] not in FIRST_TRY or row["mood"] not in KNOWN_MOODS:
            raise ValueError(f"Unrecognized tags: {uid}")
        if any(phrase in sentence.lower() for phrase in BANNED):
            raise ValueError(f"Unwanted word: {uid}")
        if re.search(r"(?:\bhttps?://|@\w+)",sentence,re.I):
            raise ValueError(f"Link or commercial handle: {uid}")
        ids.add(uid)
        texts.add(sentence)
    return tuple(result)


@lru_cache(maxsize=1)
def by_topic():
    out = {k: [] for k in FIRST_TRY}
    for item in bank():
        out[item["risk"]].append(item)
    return out


def init(connect):
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS joke_sent(
                    user_id INTEGER NOT NULL,
                    joke_id TEXT NOT NULL,
                    serial INTEGER NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id,joke_id)
                    )""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_joke_sent_user
                     ON joke_sent(user_id, serial DESC)""")


def select(connect,user_id,risk,serial):
    """Choose a topic-matching unused joke; spill over only when exhausted.

    The database enforces uniqueness per user for all 3000, until all are
    exhausted and we recycle the very oldest sent captions.
    """
    if risk not in FIRST_TRY:
        risk = "general"
    with connect() as c:
        seen = {r[0] for r in c.execute(
            "SELECT joke_id FROM joke_sent WHERE user_id=?", (user_id,))}
    eligible = [j for j in by_topic()[risk] if j["id"] not in seen]
    if not eligible:
        eligible = [j for j in bank() if j["id"] not in seen]
    if not eligible:
        # Every caption was used; allow a new cycle while keeping the
        # most recent 180 unseen until later, to avoid jarring repeats.
        with connect() as c:
            latest = {r[0] for r in c.execute(
                "SELECT joke_id FROM joke_sent WHERE user_id=?"
                " ORDER BY serial DESC LIMIT 180", (user_id,))}
        eligible = [j for j in by_topic()[risk] if j["id"] not in latest]
        if not eligible:
            eligible = list(by_topic()[risk])
    rng = random.Random(f"{user_id}:{serial}:{risk}:3000")
    return eligible[rng.randrange(len(eligible))]


def mark_sent(connect,user_id,joke_id,serial):
    with connect() as c:
        c.execute("""INSERT INTO joke_sent(user_id,joke_id,serial)
                     VALUES(?,?,?)
                     ON CONFLICT(user_id,joke_id)
                     DO UPDATE SET serial=excluded.serial,
                                   sent_at=CURRENT_TIMESTAMP""",
                  (user_id,joke_id,serial))


def count():
    return len(bank())


if __name__ == "__main__":
    from collections import Counter
    rows = bank()
    for tag, n in Counter(x["risk"] for x in rows).items():
        print(tag,n)
    print("Unique one-sentence jokes:",len(rows))
