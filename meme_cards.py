"""Free offline meme-card rendering. Optional user-provided background: assets/putin.jpg."""
from io import BytesIO
from pathlib import Path
import random
import json
import urllib.parse
import urllib.request
import html
import textwrap
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).parent
CACHE = ROOT / "assets" / "photo_cache"
API = "https://commons.wikimedia.org/w/api.php"
SEARCHES = ("Vladimir Putin portrait", "Vladimir Putin smiling", "Vladimir Putin meeting")

def _request(url):
    req = urllib.request.Request(url, headers={"User-Agent": "DependenceTrackerMemeBot/1.0 (photo cache)"})
    with urllib.request.urlopen(req, timeout=12) as response:
        return response.read()

def _commons_photo(variant):
    CACHE.mkdir(parents=True, exist_ok=True)
    existing = sorted(CACHE.glob("putin_*.jpg"))
    if len(existing) >= 5:
        return existing[variant % len(existing)]
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": SEARCHES[variant % len(SEARCHES)],
        "gsrnamespace": 6, "gsrlimit": 20,
        "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
        "iiurlwidth": 1100,
        "iiextmetadatafilter": "LicenseShortName",
    }
    data = json.loads(_request(API + "?" + urllib.parse.urlencode(params)))
    candidates = []
    for page in data.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        lic = html.unescape(info.get("extmetadata", {}).get("LicenseShortName", {}).get("value", "")).lower()
        if lic not in ("public domain", "cc0", "cc0 1.0", "pd"):
            continue
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        url = info.get("thumburl") or info.get("url")
        hostname = urllib.parse.urlsplit(url or "").hostname or ""
        if hostname == "upload.wikimedia.org":
            candidates.append(url)
    random.Random(variant + len(existing)).shuffle(candidates)
    for url in candidates[:8]:
        try:
            raw = _request(url)
            if len(raw) > 9_000_000:
                continue
            with Image.open(BytesIO(raw)) as img:
                img.verify()
            target = CACHE / f"putin_{len(existing):02d}.jpg"
            with Image.open(BytesIO(raw)) as img:
                img.convert("RGB").save(target, "JPEG", quality=88)
            return target
        except Exception:
            continue
    return existing[variant % len(existing)] if existing else None

def background_photo(variant):
    manual = ROOT / "assets" / "putin.jpg"
    if manual.is_file():
        return manual
    try:
        return _commons_photo(variant)
    except Exception:
        existing = sorted(CACHE.glob("putin_*.jpg"))
        return existing[variant % len(existing)] if existing else None

FONT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
LABELS = {"Мефедрон": "МЕФА", "Альфа-ПВП": "СОЛИ", "Каннабис": "ТРАВЫ", "Амфетамин": "АМФЕТА"}
MEMES = ["КРУТОЙ ПЕРЕЦ", "ЛЕГЕНДА РАЙОНА", "ОПЕРАЦИЯ ИДЁТ ПО ПЛАНУ", "ТЯГА НЕ ПРОШЛА ФЕЙС-КОНТРОЛЬ", "ЗДРАВЫЙ СМЫСЛ +100"]

def font(size):
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    raise RuntimeError("Install fonts-dejavu-core to render Cyrillic meme images")

def card(days: int, substance: str, variant: int = 0) -> bytes:
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
    d.text((480, 70), "НЕ СЕГОДНЯ / МЕМ-ТРЕКЕР", font=font(32), anchor="mm", fill="white")
    label = LABELS.get(substance, substance.upper())
    headline = f"{days} ДНЕЙ БЕЗ {label}" if days != 1 else f"1 ДЕНЬ БЕЗ {label}"
    # Fit long names into the canvas
    size = 66
    while size > 28 and d.textbbox((0,0), headline, font=font(size))[2] > 890:
        size -= 2
    d.text((480, 712), headline, font=font(size), anchor="mm", fill="white", stroke_width=2, stroke_fill="#000000")
    slogan = MEMES[variant % len(MEMES)]
    parts = textwrap.wrap(slogan, width=22)
    for i, part in enumerate(parts[:3]):
        d.text((480, 800 + i*53), part, font=font(42), anchor="mm", fill="#ffdf58", stroke_width=1, stroke_fill="#000000")
    d.text((480, 930), "ПО ДАННЫМ ТВОЕГО ЖУРНАЛА • НЕ МЕДИЦИНСКАЯ ОЦЕНКА", font=font(18), anchor="mm", fill="#c7c7cc")
    output = BytesIO()
    im.save(output, format="PNG", optimize=True)
    return output.getvalue()
