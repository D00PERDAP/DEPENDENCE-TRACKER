"""Free offline meme-card rendering. Optional user-provided background: assets/putin.jpg."""
from io import BytesIO
from pathlib import Path
import random
import json
import urllib.parse
import urllib.request
import html
import hashlib
import textwrap
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).parent
CACHE = ROOT / "assets" / "photo_cache"
API = "https://commons.wikimedia.org/w/api.php"
SEARCHES = ("Vladimir Putin", "Putin Kremlin", "Putin meeting", "Putin smiling", "Putin speech")

def _request(url):
    req = urllib.request.Request(url, headers={"User-Agent": "DependenceTrackerMemeBot/1.0 (photo cache)"})
    with urllib.request.urlopen(req, timeout=12) as response:
        return response.read()

def _commons_photo(variant):
    """Download a new Wikimedia image when possible, otherwise rotate cached photos."""
    CACHE.mkdir(parents=True, exist_ok=True)
    existing = sorted(CACHE.glob("putin_*.jpg"))
    if len(existing) >= 24:
        return random.choice(existing)
    # Wikimedia file search is more reliable without requiring a specific license string.
    # Keep only freely reusable images and store source attribution alongside the file.
    for term in random.sample(SEARCHES, len(SEARCHES)):
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": 'filetype:bitmap ' + term, "gsrnamespace": 6, "gsrlimit": 40,
            "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
            "iiurlwidth": 1100,
        }
        try:
            data = json.loads(_request(API + "?" + urllib.parse.urlencode(params)))
        except Exception:
            continue
        candidates = []
        for page in data.get("query", {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            metadata = info.get("extmetadata") or {}
            lic = html.unescape(metadata.get("LicenseShortName", {}).get("value", "")).lower()
            if not (lic.startswith("cc by") or lic in ("public domain", "cc0", "cc0 1.0", "pd")):
                continue
            if info.get("mime") not in ("image/jpeg", "image/png"):
                continue
            url = info.get("thumburl") or info.get("url")
            if (urllib.parse.urlsplit(url or "").hostname or "") != "upload.wikimedia.org":
                continue
            candidates.append((url, page.get("title", ""), lic))
        random.shuffle(candidates)
        for url, title, lic in candidates:
            key = hashlib.sha256(url.encode()).hexdigest()[:16]
            target = CACHE / f"putin_{key}.jpg"
            if target.exists():
                continue
            try:
                raw = _request(url)
                if len(raw) > 9_000_000:
                    continue
                with Image.open(BytesIO(raw)) as img:
                    img.verify()
                with Image.open(BytesIO(raw)) as img:
                    if min(img.size) < 320:
                        continue
                    img.convert("RGB").save(target, "JPEG", quality=87)
                (CACHE / f"putin_{key}.txt").write_text(
                    f"{title} | {lic} | https://commons.wikimedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
                    encoding="utf-8",
                )
                existing.append(target)
                return target
            except Exception:
                continue
    return random.choice(existing) if existing else None

def background_photo(variant):
    manual = ROOT / "assets" / "putin.jpg"
    try:
        found = _commons_photo(variant)
        if found is not None:
            return found
    except Exception:
        pass
    existing = sorted(CACHE.glob("putin_*.jpg"))
    return random.choice(existing) if existing else (manual if manual.is_file() else None)

FONT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
LABELS = {"Мефедрон": "МЕФА", "Альфа-ПВП": "СОЛИ", "Каннабис": "ТРАВЫ", "Амфетамин": "АМФЕТА"}
MEMES = [
    "НЕ ПИШИ БЫВШЕЙ. ЭТО ПРИКАЗ",
    "ПОПЕЙ ВОДЫ И ПРОВЕРЬ САМОЧУВСТВИЕ",
    "ГЕНИАЛЬНАЯ ИДЕЯ? ЗАПИШИ И РЕШИ ЗАВТРА",
    "ТРИ ЧАСА НОЧИ. НИКАКИХ ВАЖНЫХ РЕШЕНИЙ",
    "ПУТИН УЗНАЛ, ЧТО ТЫ ОПЯТЬ НЕ СПАЛ",
    "ТЕЛЕФОН В СТОРОНУ. НЕ СОЗДАВАЙ СЮЖЕТ",
    "НЕ САДИСЬ ЗА РУЛЬ. ДАЖЕ ЕСЛИ ТЫ ШУМАХЕР",
    "ПЛАН НА ВЕЧЕР: БЕЗ ЛИШНИХ ПРИКЛЮЧЕНИЙ",
    "НЕ СМЕШИВАЙ. ЭТО НЕ КОКТЕЙЛЬ",
    "СНА НЕТ? ОТМЕНЯЕМ ВЕЛИКИЕ РЕШЕНИЯ",
    "ПОЧУВСТВОВАЛ СЕБЯ ПЛОХО? ЗОВИ ПОМОЩЬ",
    "ГЛАВНЫЙ КВЕСТ: ДОБРАТЬСЯ ДО ЗАВТРА БЕЗ БЕД",
]

def font(size):
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    raise RuntimeError("Install fonts-dejavu-core to render Cyrillic meme images")

def card(days: int, substance: str, variant: int = 0) -> bytes:
    pack = ROOT / "assets" / "meme_pack"
    ready = sorted(list(pack.glob("*.jpg")) + list(pack.glob("*.png")) + list(pack.glob("*.webp")))
    if ready:
        previous = getattr(card, "_previous", None)
        choices = [p for p in ready if p != previous] or ready
        chosen = random.choice(choices)
        card._previous = chosen
        with Image.open(chosen) as source:
            output = BytesIO()
            source.convert("RGB").save(output, format="PNG")
            return output.getvalue()
    w, h = 960, 960
    im = Image.new("RGB", (w,h), "#101014")
    bg = background_photo(variant)
    if bg is not None and bg.is_file():
        with Image.open(bg) as source:
            photo = ImageOps.fit(source.convert("RGB"), (w, 670), centering=(0.5, 0.32))
        im.paste(photo, (0, 0))
        overlay = Image.new("RGBA", (w,h), (0,0,0,0))
        od = ImageDraw.Draw(overlay)
        od.rectangle((0, 0, w, 200), fill=(0,0,0,130))
        od.rectangle((0, 580, w, h), fill=(0,0,0,225))
        im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((30, 28, 930, 110), radius=25, fill="#c52231")
    d.text((480, 70), "НЕ НАТВОРИ ДЕЛОВ", font=font(32), anchor="mm", fill="white")
    label = LABELS.get(substance, substance.upper())
    headline = "ЖУРНАЛ УПОТРЕБЛЕНИЯ" if days < 0 else f"ПОСЛЕДНЯЯ ЗАПИСЬ: {days} ДН. НАЗАД"
    # Fit long names into the canvas
    size = 66
    while size > 28 and d.textbbox((0,0), headline, font=font(size))[2] > 890:
        size -= 2
    d.text((480, 712), headline, font=font(size), anchor="mm", fill="white", stroke_width=2, stroke_fill="#000000")
    slogan = random.choice(MEMES)
    parts = textwrap.wrap(slogan, width=24)
    for i, part in enumerate(parts[:3]):
        d.text((480, 800 + i*53), part, font=font(42), anchor="mm", fill="#ffdf58", stroke_width=1, stroke_fill="#000000")
    d.text((480, 920), "ПО ДАННЫМ ЖУРНАЛА • НЕ МЕДИЦИНСКАЯ ОЦЕНКА", font=font(17), anchor="mm", fill="#c7c7cc")
    d.text((480, 946), "ФОТО: WIKIMEDIA COMMONS / АВТОРЫ НА СТРАНИЦАХ ФАЙЛОВ • КОЛЛАЖ ИЗМЕНЁН", font=font(13), anchor="mm", fill="#c7c7cc")
    output = BytesIO()
    im.save(output, format="PNG", optimize=True)
    return output.getvalue()
