"""Context-aware parody meme cards. Optional Groq text API, offline fallback."""
import asyncio
import json
import os
import random
import re
import urllib.request
import urllib.parse
import hashlib
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
    portraits = base / "politicians"
    portraits.mkdir(parents=True, exist_ok=True)
    files = [p for p in portraits.glob("*.jpg")]
    # Try a new public-domain or Creative Commons portrait until the cache is full.
    if len(files) < 24:
        names = ["Vladimir Putin", "Dmitry Medvedev", "Sergey Lavrov", "Vladimir Zhirinovsky"]
        random.shuffle(names)
        for name in names:
            try:
                params = urllib.parse.urlencode({
                    "action": "query", "format": "json", "generator": "search",
                    "gsrsearch": name, "gsrnamespace": 6, "gsrlimit": 30,
                    "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
                    "iiurlwidth": 1000,
                })
                req = urllib.request.Request("https://commons.wikimedia.org/w/api.php?" + params,
                                            headers={"User-Agent": "DependenceTrackerBot/1.0"})
                with urllib.request.urlopen(req, timeout=8) as res:
                    data = json.load(res)
                candidates = list(data.get("query", {}).get("pages", {}).values())
                random.shuffle(candidates)
                for page in candidates:
                    info = (page.get("imageinfo") or [{}])[0]
                    license_name = (info.get("extmetadata", {}).get("LicenseShortName", {}).get("value", "")).lower()
                    if not (license_name.startswith("cc by") or license_name in ("public domain", "cc0", "cc0 1.0")):
                        continue
                    url = info.get("thumburl") or info.get("url")
                    if info.get("mime") not in ("image/jpeg", "image/png"):
                        continue
                    if (urllib.parse.urlsplit(url or "").hostname or "") != "upload.wikimedia.org":
                        continue
                    dest = portraits / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".jpg")
                    if dest.exists():
                        continue
                    req = urllib.request.Request(url, headers={"User-Agent": "DependenceTrackerBot/1.0"})
                    with urllib.request.urlopen(req, timeout=8) as res:
                        data = res.read(8_000_001)
                    if len(data) > 8_000_000:
                        continue
                    with Image.open(BytesIO(data)) as img:
                        if min(img.size) < 300:
                            continue
                        img.convert("RGB").save(dest, "JPEG", quality=88)
                    (portraits / (dest.stem + ".txt")).write_text(
                        page.get("title", "") + " | " + license_name +
                        " | https://commons.wikimedia.org/wiki/" +
                        urllib.parse.quote(page.get("title", "").replace(" ", "_")), encoding="utf-8")
                    files.append(dest)
                    return dest
            except Exception:
                continue
    if files:
        return random.choice(files)
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
