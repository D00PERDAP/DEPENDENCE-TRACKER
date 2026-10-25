"""Offline-first PICTURE memes. Old-school photo + Impact captions, never quote cards.

Default startup produces original humorous cartoon-photo compositions without any
network. A background upgrade optionally downloads a *small curated set* of
Unsplash-licensed animal/objects photos for transformed caption memes. No API key,
scraping arbitrary sites, unlicensed celebrity images or recognizable people.
"""
from __future__ import annotations

import hashlib
import io
import os
import random
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets" / "memes"
CACHE = ROOT / "data" / "stock_photo_cache"
SIZE = 850
GUARD = threading.Lock()

# IDs of ordinary Unsplash photos (animals, objects, landscapes). These are NOT
# photos of people using drugs. When downloaded, files are transformed into memes,
# not published as a competing stock-photo library.
PHOTO = {
    "cat": "photo-1514888286974-6c03e2ca1dba",
    "cat2": "photo-1573865526739-10659fec78a5",
    "cat3": "photo-1533738363-b7f9aef128ce",
    "dog": "photo-1517849845537-4d257902454a",
    "dog2": "photo-1583337130417-3346a1be7dee",
    "dogs": "photo-1548199973-03cce0bbc87b",
    "phone": "photo-1511707171634-5f897ff02aa9",
    "office": "photo-1516321318423-f06f85e504b3",
    "bank": "photo-1554224155-6726b3ff858f",
    "room": "photo-1505693416388-ac5ce068fe85",
    "night": "photo-1519608487953-e999c86e7455",
    "coffee": "photo-1495474472287-4d71bcdd2085",
    "city": "photo-1519501025264-65ba15a82390",
}

@dataclass(frozen=True)
class Visual:
    sub: str
    risk: str
    scene: str
    top: str
    bottom: str
    photo: str

# Every entry has a *visual scene*, not plain text on a background.
# Tags match context_memes.SUB_KEYS and context_memes.RISK_KEYS.
MEMES = (
    Visual("general", "more", "cat", "Я: ЕЩЁ НЕМНОГО И ВСЁ", "МОЗГ: ЗАПИСАЛ КАК СЕЗОН 2", "cat2"),
    Visual("general", "more", "dog", "КОГДА РЕШИЛ НЕ ПРОДОЛЖАТЬ", "А МОЗГ УЖЕ ОБЗВАНИВАЕТ СПОНСОРОВ", "dog2"),
    Visual("general", "ex", "phone", "Я В 04:20: НАПИШУ БЫВШЕЙ", "ТЕЛЕФОН: БЛЯТЬ, НЕ НАДО", "phone"),
    Visual("general", "ex", "cat", "КОГДА НАЧАЛ ПИСАТЬ «ПРИВЕТ»", "И ДАЖЕ КОТ УШЁЛ ИЗ КОМНАТЫ", "cat"),
    Visual("general", "contacts", "phone", "СЕДЬМОЕ ГОЛОСОВОЕ В 4 УТРА", "ДРУЗЬЯ УЖЕ ИЩУТ КНОПКУ «ЗАБЛОКИРОВАТЬ»", "phone"),
    Visual("general", "contacts", "dog", "Я: БРАТ, СЛУШАЙ, Я ВСЁ ПОНЯЛ", "БРАТ: ТЫ ВЧЕРА 8 РАЗ ЭТО ГОВОРИЛ", "dog"),
    Visual("general", "money", "wallet", "ОТКРЫЛ ПРИЛОЖЕНИЕ БАНКА УТРОМ", "ЖАНР СМЕНИЛСЯ НА УЖАСЫ", "bank"),
    Visual("general", "money", "cat", "КОШЕЛЁК ПОСЛЕ ВЕЧЕРА", "ПРОСИТ ПЕРЕВЕСТИ ЕГО В ПРИЮТ", "cat3"),
    Visual("general", "travel", "car", "Я: СЕЙЧАС КУДА-НИБУДЬ ПОЕДУ", "ЗДРАВЫЙ СМЫСЛ: ТОЛЬКО НЕ ЗА РУЛЬ", "night"),
    Visual("general", "travel", "dog", "КОГДА СОБРАЛСЯ ЗА ПРИКЛЮЧЕНИЯМИ", "ДАЖЕ СОБАКА СКАЗАЛА: СИДИ ДОМА", "dog2"),
    Visual("general", "sleep", "bed", "04:58. Я ПЫТАЮСЬ СПАТЬ", "МОЗГ: А ПОМНИШЬ ПОЗОР В 2018?", "room"),
    Visual("general", "sleep", "cat", "МОЙ МОЗГ ПЕРЕД СНОМ", "ОБЪЯВИЛ НОЧНУЮ ПЛАНЁРКУ НА 6 ЧАСОВ", "cat"),
    Visual("general", "paranoia", "eye", "КОГДА КАЖЕТСЯ, ЧТО ВСЕ СЛЕДЯТ", "А ЭТО ПРОСТО ХОЛОДИЛЬНИК ШУМИТ", "cat3"),
    Visual("general", "paranoia", "cat", "Я: ЧТО-ТО ТУТ НЕ ТАК", "КОТ: БРАТАН, ТЫ НЕ СПАЛ", "cat2"),
    Visual("general", "check", "dog", "БОТ: НЕ ТВОРИ ХЕРНЮ", "Я: ЛАДНО, СЕГОДНЯ БЕЗ СЮЖЕТНЫХ ПОВОРОТОВ", "dog"),
    Visual("general", "check", "cat", "СЕГОДНЯ МОЖНО ОБОЙТИСЬ", "БЕЗ ЗВОНКА БЫВШЕЙ И КРЕДИТА", "cat"),
    Visual("general", "general", "cat", "Я: ВСЁ ПОД КОНТРОЛЕМ", "КОНТРОЛЬ: МЕНЯ ТУТ ВООБЩЕ НЕТ", "cat"),
    Visual("general", "general", "dog", "НОЧНОЙ Я: ГЛАВНЫЙ ГЕРОЙ", "УТРЕННИЙ Я: ПРОСТО СВИДЕТЕЛЬ", "dog2"),
    Visual("general", "after", "cat", "ПРОСНУЛСЯ ПОСЛЕ ТУСОВКИ", "В ТЕЛЕФОНЕ УЖЕ ЕСТЬ ДОКАЗАТЕЛЬСТВА", "cat3"),
    Visual("general", "ok", "dog", "НИКОМУ НЕ ПОЗВОНИЛ И НИЧЕГО НЕ КУПИЛ", "НЕОЖИДАННЫЙ ПОВОРОТ СЕЗОНА", "dog"),
    Visual("meph", "general", "cat", "МЕФ: ЩАС БУДЕТ ГЕНИАЛЬНАЯ ИДЕЯ", "КОШЕЛЁК: НАХУЙ НАДО", "cat2"),
    Visual("meph", "more", "cat", "МЕФ: МОЖЕТ, ЕЩЁ?", "Я ЗАВТРА: МОЖЕТ, НЕ НАДО БЫЛО?", "cat3"),
    Visual("meph", "ex", "phone", "МЕФ В 4 УТРА: НАПИШИ ЕЙ", "МОЙ ТЕЛЕФОН: Я ТЕБЯ УМОЛЯЮ", "phone"),
    Visual("meph", "money", "wallet", "ПЛАН НА НЕДЕЛЮ: ЕДА И КВАРТИРА", "МЕФ: ИНТЕРЕСНЫЙ ПЛАН. БЫЛ.", "bank"),
    Visual("meph", "sleep", "bed", "МЕФ: НИКАКОГО СНА СЕГОДНЯ", "МОЗГ: А Я УЖЕ И НЕ СПРАШИВАЛ", "room"),
    Visual("alpha", "general", "eye", "СОЛЕВОЙ ДЕТЕКТИВ В 05:00", "РАССЛЕДУЕТ, ПОЧЕМУ ШТОРА ШЕВЕЛИТСЯ", "cat3"),
    Visual("alpha", "paranoia", "eye", "Я УЖЕ ПОЛЧАСА СЛЕЖУ ЗА ШТОРОЙ", "ШТОРА: Я ПРОСТО ШТОРА, БРАТ", "cat2"),
    Visual("alpha", "more", "cat", "МОЗГ: ДАВАЙ ЕЩЁ ОДИН ЗАХОД", "ЗДРАВЫЙ СМЫСЛ: Я УВОЛИЛСЯ", "cat"),
    Visual("stimulant", "general", "coffee", "РАЗГОН МОЗГА НА 300 КМ/Ч", "А ВАЖНЫЕ РЕШЕНИЯ ЛУЧШЕ ОТЛОЖИТЬ", "coffee"),
    Visual("stimulant", "sleep", "bed", "СПАТЬ? КТО ТАКОЙ СПАТЬ?", "БУДИЛЬНИК: СКОРО ПОЗНАКОМИМСЯ", "room"),
    Visual("stimulant", "more", "dog", "СКОРОСТЬ МЫСЛЕЙ: КОСМОС", "СКОРОСТЬ ЗДРАВОГО СМЫСЛА: 0", "dog2"),
    Visual("mdma", "general", "dog", "ВЧЕРА ЛЮБИЛ ВЕСЬ МИР", "СЕГОДНЯ ХОЧУ ЛЮБИТЬ ТОЛЬКО ОДЕЯЛО", "dogs"),
    Visual("mdma", "contacts", "phone", "ВЧЕРА ПРИЗНАЛСЯ В ЛЮБВИ ВСЕМ", "ВКЛЮЧАЯ ТАКСИСТА И КОТЛЕТУ", "phone"),
    Visual("weed", "general", "cat", "ЧАС ВЫБИРАЛ КИНО", "ПОСМОТРЕЛ ТОЛЬКО МЕНЮ", "cat"),
    Visual("weed", "paranoia", "eye", "Я: МЕНЯ КТО-ТО СЛЫШИТ?", "ХОЛОДИЛЬНИК: ТОЛЬКО Я, ДРУГ", "cat3"),
    Visual("alcohol", "general", "dog", "«ДА МЫ НОРМАЛЬНО СИДЕЛИ»", "СЛОВА, ПОСЛЕ КОТОРЫХ БУДУТ УЛИКИ", "dog2"),
    Visual("alcohol", "money", "wallet", "СЧЁТ В БАРЕ ПОСЛЕ 4 УТРА", "ПОХОЖ НА ВЫКУП ЗА ЗАЛОЖНИКА", "bank"),
    Visual("alcohol", "ex", "phone", "КОГДА ВЫПИЛ И РЕШИЛ НАПИСАТЬ БЫВШЕЙ", "ПАЛЕЦ, НЕМЕДЛЕННО ОТОЙДИ ОТ КНОПКИ", "phone"),
    Visual("ketamine", "general", "cat", "В ГОЛОВЕ КОСМИЧЕСКАЯ ОДИССЕЯ", "В КОМНАТЕ ПРОСТО ЗАКОНЧИЛСЯ ЧАЙ", "cat2"),
    Visual("opioid", "general", "dog", "НЕ ГЕРОЙСТВУЙ В ОДИНОЧКУ", "ЛУЧШЕ ПОЗВАТЬ ЧЕЛОВЕКА, КОТОРОМУ ДОВЕРЯЕШЬ", "dog"),
    Visual("sedative", "general", "bed", "ИДЕЯ ДНЯ: НИЧЕГО НЕ СМЕШИВАТЬ", "МОЙ МОЗГ ВПЕРВЫЕ НЕ СПОРИТ", "room"),
    Visual("psychedelic", "general", "cat", "ИСКАЛ ОТВЕТЫ В ДРУГИХ ИЗМЕРЕНИЯХ", "А ПОТОМ ИСКАЛ НОСКИ", "cat3"),
)

COLORS = [
    ("#F3C6BC", "#C4647A"), ("#BCD8DB", "#486B94"),
    ("#D3D0F7", "#6D55A5"), ("#EBD6AB", "#AC704C"),
    ("#BFDABF", "#426D59"), ("#F4D2E7", "#9163A7"),
]


def font(size):
    choices = (
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
    )
    for path in choices:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _shape(draw, pts, fill, width=0, outline=None):
    draw.polygon(pts, fill=fill)
    if outline:
        draw.line(list(pts) + [pts[0]], fill=outline, width=width, joint="curve")


def cartoon(scene, number):
    """Render actual expressive pictured scenes: cat/dog + relevant objects."""
    bg1, bg2 = COLORS[number % len(COLORS)]
    image = Image.new("RGB", (SIZE, SIZE), bg1)
    d = ImageDraw.Draw(image)
    # Dirty retro texture and tilted color panels for old meme aesthetic.
    d.polygon([(0, 0), (SIZE, 0), (SIZE, 500), (0, 740)], fill=bg2)
    d.ellipse((-120, 190, 350, 650), fill="#FFFFFF", outline="#272334", width=10)
    d.ellipse((560, 320, 1120, 870), fill="#F3E8B7", outline="#272334", width=12)
    # Big expressive cat/dog doodle instead of a text-only card.
    animal = scene not in {"phone", "wallet", "car", "bed", "eye", "coffee"}
    if scene in ("dog", "car"):
        d.ellipse((215, 160, 700, 650), fill="#D8A575", outline="#1B1D24", width=14)
        d.ellipse((140, 160, 340, 410), fill="#A47456", outline="#1B1D24", width=12)
        d.ellipse((600, 160, 790, 420), fill="#A47456", outline="#1B1D24", width=12)
    else:
        _shape(d, [(192, 322), (227, 110), (380, 235)], "#E2BC92", 13, "#1B1D24")
        _shape(d, [(540, 232), (710, 102), (746, 365)], "#E2BC92", 13, "#1B1D24")
        d.ellipse((188, 214, 747, 698), fill="#E2BC92", outline="#1B1D24", width=14)
    d.ellipse((300, 342, 392, 466), fill="white", outline="#1B1D24", width=11)
    d.ellipse((530, 342, 622, 466), fill="white", outline="#1B1D24", width=11)
    shift = 20 if number % 2 else -12
    d.ellipse((344 + shift, 385, 371 + shift, 441), fill="#12151A")
    d.ellipse((574 + shift, 385, 601 + shift, 441), fill="#12151A")
    if scene in ("eye", "cat", "cat2", "cat3"):
        d.line((327, 310, 385, 287), fill="#12151A", width=13)
        d.line((545, 288, 613, 311), fill="#12151A", width=13)
    d.polygon([(430, 505), (490, 505), (460, 539)], fill="#BD6073")
    d.arc((409, 525, 516, 610), 8, 172, fill="#12151A", width=9)
    if number % 4 == 0:
        d.arc((425, 560, 510, 623), 0, 180, fill="#12151A", width=9)
    for x in (210, 570):
        for dy in (520, 558):
            d.line((x, dy, x + 145, dy - 15), fill="#20212A", width=5)

    if scene == "phone":
        d.rounded_rectangle((35, 356, 290, 820), radius=32, fill="#151824", outline="white", width=11)
        d.rounded_rectangle((54, 395, 271, 755), radius=15, fill="#E9EFFF")
        d.rounded_rectangle((76, 440, 247, 506), radius=22, fill="#B8D6FB")
        d.rounded_rectangle((91, 535, 237, 590), radius=20, fill="#EDAAA9")
        d.ellipse((115, 645, 197, 727), fill="#D83C59")
        d.line((153, 670, 153, 695), fill="white", width=10)
    elif scene == "wallet":
        d.rounded_rectangle((25, 610, 360, 806), radius=30, fill="#543C30", outline="#17151A", width=13)
        d.rounded_rectangle((180, 640, 383, 766), radius=20, fill="#89603D", outline="#16171C", width=7)
        d.ellipse((250, 686, 285, 722), fill="#F6C95D")
        d.text((73, 660), "0", font=font(85), fill="#FFF4E1")
    elif scene == "car":
        d.rounded_rectangle((40, 645, 370, 755), radius=35, fill="#D24D56", outline="#16171C", width=12)
        d.polygon([(95, 650), (150, 560), (270, 560), (333, 650)], fill="#B8E9EC")
        for cx in (125, 300):
            d.ellipse((cx-33, 727, cx+33, 797), fill="#151515", outline="#FDFDFD", width=7)
    elif scene == "bed":
        d.rectangle((20, 672, 394, 780), fill="#DFE9F4", outline="#16171C", width=12)
        d.rounded_rectangle((45, 585, 158, 700), radius=18, fill="#F4E7D3")
        d.line((38, 782, 38, 842), fill="#16171C", width=16)
        d.text((85, 490), "Zzz", font=font(75), fill="#1B1D24")
    elif scene == "eye":
        for x in (110, 215, 330):
            d.ellipse((x-45, 710, x+40, 790), fill="#FFFDF1", outline="#191B20", width=8)
            d.ellipse((x-12, 735, x+22, 773), fill="#1C1C1C")
    elif scene == "coffee":
        d.rounded_rectangle((30, 660, 290, 811), radius=18, fill="#E8E4DE", outline="#16171C", width=12)
        d.arc((210, 670, 364, 812), -95, 95, fill="#1B1D24", width=18)
        for x in (94, 160, 232):
            d.arc((x, 575, x + 40, 674), -130, 40, fill="#FDF7F4", width=8)
    else:
        d.ellipse((95, 625, 245, 765), fill="#FFF7ED", outline="#1B1D24", width=10)
        d.ellipse((127, 652, 214, 739), fill="#D2A979")
    return image


def _wrap(d, text, ft, max_width):
    words = text.split()
    lines = []
    cur = ""
    for word in words:
        proposed = (cur + " " + word).strip()
        if cur and d.textbbox((0, 0), proposed, font=ft, stroke_width=3)[2] > max_width:
            lines.append(cur)
            cur = word
        else:
            cur = proposed
    if cur:
        lines.append(cur)
    return lines


def _caption(draw, text, is_top):
    for size in range(59, 28, -2):
        ft = font(size)
        lines = _wrap(draw, text, ft, SIZE - 48)
        max_lines = 3
        if len(lines) <= max_lines:
            break
    h = size * 1.23
    y = 24 if is_top else SIZE - (len(lines) * h) - 28
    # Dark soft shadow + white text with bold black outline, Impact meme style.
    for line in lines:
        draw.text((SIZE // 2 + 2, round(y) + 3), line, font=ft, fill="#0D1011",
                  anchor="mt", stroke_width=8, stroke_fill="#0D1011")
        draw.text((SIZE // 2, round(y)), line, font=ft, fill="white",
                  anchor="mt", stroke_width=5, stroke_fill="#0D1011")
        y += h


def render(item, target, photo=None, number=0):
    if photo is None:
        image = cartoon(item.scene, number)
    else:
        image = ImageOps.fit(photo.convert("RGB"), (SIZE, SIZE),
                             method=Image.Resampling.LANCZOS, centering=(.5, .46))
    d = ImageDraw.Draw(image)
    _caption(d, item.top, True)
    _caption(d, item.bottom, False)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    try:
        image.save(temp, "JPEG", quality=81, optimize=True)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


def _target(item, suffix, media):
    txt = "|".join((item.sub, item.risk, item.scene, item.top, item.bottom, item.photo))
    digest = hashlib.sha256(txt.encode("utf-8")).hexdigest()[:14]
    return Path(media) / "contextual" / item.sub / item.risk / f"{suffix}_{digest}.jpg"


def ensure_pack(media=ASSETS, limit=None):
    """Always works offline. Builds illustrated *picture memes* with captions."""
    done = []
    with GUARD:
        for idx, item in enumerate(MEMES[:limit] if limit is not None else MEMES):
            path = _target(item, "illustrated", media)
            if not path.is_file() or path.stat().st_size < 1000:
                render(item, path, number=idx)
            done.append(path)
    return done


def inline_picture(substance, risk):
    """Make a JPEG in memory if service cannot write to the assets directory.

    Guarantees that an unavailable/unwritable disk library need not turn a meme
    into plain text. No file writes, no network and no external credentials.
    """
    from context_memes import SUB_KEYS, RISK_KEYS
    sub = SUB_KEYS.get(substance, "general")
    risk = risk if risk in RISK_KEYS else "general"
    exact = [(index, item) for index, item in enumerate(MEMES)
             if item.sub == sub and item.risk == risk]
    matched_risk = [(index, item) for index, item in enumerate(MEMES)
                    if item.sub == "general" and item.risk == risk]
    matched_sub = [(index, item) for index, item in enumerate(MEMES)
                   if item.sub == sub and item.risk == "general"]
    general = [(index, item) for index, item in enumerate(MEMES)
               if item.sub == "general" and item.risk == "general"]
    options = exact or matched_risk or matched_sub or general
    if not options:
        raise RuntimeError("No visual memes configured")
    index, item = random.choice(options)
    image = cartoon(item.scene, index)
    draw = ImageDraw.Draw(image)
    _caption(draw, item.top, True)
    _caption(draw, item.bottom, False)
    output = io.BytesIO()
    image.save(output, "JPEG", quality=83)
    return output.getvalue()


def _download_photo(key, cache=CACHE):
    if key not in PHOTO:
        return None
    cache = Path(cache)
    path = cache / (PHOTO[key] + ".jpg")
    if path.exists() and path.stat().st_size > 2000:
        try:
            with Image.open(path) as img:
                img.verify()
            return path
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    url = "https://images.unsplash.com/" + PHOTO[key] + "?w=950&q=78&fit=crop"
    request = urllib.request.Request(url, headers={"User-Agent": "AntiTrashBot/2.1 (meme transform)"})
    try:
        with urllib.request.urlopen(request, timeout=7) as response:
            if response.status != 200 or not (response.headers.get("Content-Type", "").lower().startswith("image/")):
                return None
            data = response.read(3_500_001)
        if not data or len(data) > 3_500_000:
            return None
        with Image.open(io.BytesIO(data)) as photo:
            photo.verify()
        with Image.open(io.BytesIO(data)) as photo:
            if min(photo.size) < 300 or photo.width * photo.height > 12_000_000:
                return None
            clean = ImageOps.exif_transpose(photo).convert("RGB")
            clean.thumbnail((950, 950))
            tmp = path.with_name(path.name + ".tmp")
            try:
                clean.save(tmp, "JPEG", quality=81)
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        return path
    except (OSError, TimeoutError, ValueError, UnidentifiedImageError):
        return None


def upgrade_photos(media=ASSETS, cache=CACHE):
    """Gradually replace original cartoons with permissively licensed meme photos.

    Publish each finished photo immediately, not after *all* downloads finish.
    First-run HTTPS failures are harmless: illustrated JPEGs still work offline.
    """
    if os.getenv("MEME_STOCK_PHOTOS", "1") != "1":
        return 0
    count = 0
    keys = list(dict.fromkeys(item.photo for item in MEMES))
    for key in keys:
        grouped = [(i, item) for i, item in enumerate(MEMES) if item.photo == key]
        if all(_target(item, "photo", media).is_file() for _, item in grouped):
            count += len(grouped)
            continue
        source = _download_photo(key, cache)
        if source is None:
            continue
        with GUARD:
            for i, item in grouped:
                target = _target(item, "photo", media)
                if target.is_file() and target.stat().st_size > 2000:
                    count += 1
                    continue
                try:
                    with Image.open(source) as photo:
                        render(item, target, photo=photo, number=i)
                    count += 1
                except (OSError, ValueError, UnidentifiedImageError):
                    continue
    return count


if __name__ == "__main__":
    print("Original offline picture memes:", len(ensure_pack()))
    print("Licensed-photo upgrades:", upgrade_photos())
