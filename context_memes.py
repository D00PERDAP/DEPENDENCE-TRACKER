"""Context-aware parody meme cards. Optional Groq text API, offline fallback."""
import asyncio
import json
import os
import random
import re
import urllib.request
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).parent
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ACTORS = ["Путин", "Медведев", "Лавров", "Жириновский"]
LINES = {
    "record": ["МИНИСТЕРСТВО ЗДРАВОГО СМЫСЛА: НЕ НАТВОРИ ДЕЛОВ", "КРЕМЛЬ НАПОМИНАЕТ: НЕ СМЕШИВАЙ И НЕ САДИСЬ ЗА РУЛЬ"],
    "ok": ["МЕДВЕДЕВ ДОКЛАДЫВАЕТ: ПОКА БЕЗ ПРИКЛЮЧЕНИЙ", "ГОСДУМА ПРИЗЫВАЕТ НЕ ТЕРЯТЬ БДИТЕЛЬНОСТЬ"],
    "bad": ["ЛАВРОВ ВЫРАЖАЕТ ОБЕСПОКОЕННОСТЬ. ПОЗОВИ ПОМОЩЬ", "СОСТОЯНИЕ ТЯЖЁЛОЕ? НЕ ОСТАВАЙСЯ ОДИН"],
    "history": ["ГОСДУМА ИЗУЧИЛА СТАТИСТИКУ. КОММЕНТАРИЕВ НЕ БУДЕТ", "АРХИВЫ КРЕМЛЯ ОТКРЫТЫ. БЕЗ ОСУЖДЕНИЯ"],
    "reminder": ["КРЕМЛЬ НА СВЯЗИ: КАК САМОЧУВСТВИЕ?", "ПОПЕЙ ВОДЫ, ПРОВЕРЬ СОСТОЯНИЕ. БЕЗ ПОДВИГОВ"],
    "random": ["НЕ ПИШИ БЫВШЕЙ. ЭТО ПРИКАЗ", "ВАЖНЫЕ РЕШЕНИЯ ПЕРЕНОСЯТСЯ НА ЗАВТРА", "НЕ НАТВОРИ ДЕЛОВ"]
}

def _ai_line(event):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    prompt = (
        "Придумай ОДНУ короткую русскую подпись до 85 символов для пародийного мема "
        "с российским политиком. Тема: " + event +
        ". Стиль: абсурдный государственный указ, добрый сарказм. "
        "Цель — снижение вреда, не поощрять употребление, не давать дозировок, "
        "не изображать выдуманные фразы как реальные цитаты. "
        "Не упоминай конкретного человека или его якобы настоящие слова. "
        "Ответ только подпись, без кавычек."
    )
    payload = json.dumps({"model": model, "temperature": 0.9, "max_tokens": 100,
                          "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions",
        data=payload, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            line = json.load(res)["choices"][0]["message"]["content"].strip()
        line = re.sub(r"[\\r\\n]+", " ", line).strip(" \"'«»")
        return line[:85] if len(line) >= 8 else None
    except Exception:
        return None

def _font(size):
    return ImageFont.truetype(FONT, size)

def _wrap(draw, text, font, max_width):
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = (line + " " + word).strip()
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines

def _photo(event):
    base = ROOT / "assets"
    # Prefer separate portraits: assets/politicians/{putin,medvedev,lavrov,zhirinovsky}_*.jpg
    portraits = [p for p in (base / "politicians").glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    if portraits:
        return random.choice(portraits)
    cached = list((base / "photo_cache").glob("putin_*.jpg"))
    local = base / "putin.jpg"
    if local.exists():
        cached.append(local)
    return random.choice(cached) if cached else None

def render(event="random"):
    event = event if event in LINES else "random"
    caption = _ai_line(event) or random.choice(LINES[event])
    size = (960, 960)
    im = Image.new("RGB", size, "#101115")
    photo = _photo(event)
    if photo:
        with Image.open(photo) as raw:
            pic = ImageOps.fit(raw.convert("RGB"), (960, 960), centering=(0.5, 0.35))
            im.paste(pic, (0, 0))
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, 0, 960, 360), fill=(0, 0, 0, 170))
    od.rectangle((0, 760, 960, 960), fill=(0, 0, 0, 150))
    im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(im)
    for fsize in range(68, 33, -2):
        font = _font(fsize)
        lines = _wrap(draw, caption.upper(), font, 860)
        if len(lines) <= 5 and len(lines) * (fsize + 10) <= 320:
            break
    for i, line in enumerate(lines[:5]):
        draw.text((48, 28 + i * (fsize + 10)), line, font=font, fill="white", stroke_width=2, stroke_fill="black")
    draw.text((48, 904), "ПАРОДИЯ • НЕ ОФИЦИАЛЬНАЯ ЦИТАТА", font=_font(24), fill="#f0d45b")
    out = BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()

async def image(event="random"):
    return await asyncio.to_thread(render, event)
