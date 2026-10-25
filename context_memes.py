"""Context-first meme selection, without disliked auto-generated quote cards.

Only assets added to approved local folders are sent as images. When no fitting
image exists, a clearly identified short text joke is sent instead of a fake match.
No scraped third-party images are automatically republished.
"""
from __future__ import annotations

import random
import hashlib
import io
import os
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from meme_review_store import transact

from aiogram.types import FSInputFile

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets" / "memes"
IMAGES = (".jpg", ".jpeg", ".png", ".webp")
SUB_KEYS = {
    "Мефедрон": "meph", "Альфа-ПВП": "alpha", "Амфетамин": "stimulant",
    "Метамфетамин": "stimulant", "Кокаин": "stimulant", "MDMA": "mdma",
    "Каннабис": "weed", "Алкоголь": "alcohol", "Кетамин": "ketamine",
    "Опиоиды": "opioid", "Бензодиазепины": "sedative",
    "ЛСД": "psychedelic", "Псилоцибин": "psychedelic",
    "Другое / не знаю": "general",
}
RISK_KEYS = {"more", "contacts", "ex", "money", "travel", "paranoia",
             "sleep", "check", "general", "after", "ok"}
LEGACY_CATEGORIES = {"ex": "ex", "contacts": "calls", "money": "money", "travel": "travel"}
TEXT_JOKES = {
    "more": (
        "«Да я только чуть-чуть продолжу» — самая дорогая фраза вечера.",
        "У мозга был план. У банковского приложения — скриншоты.",
        "Когда решил, что в марафоне не хватает ещё одного круга. 🥴",
    ),
    "ex": (
        "Когда хотел написать бывшей в 04:20 — даже телефон притворился разряженным.",
        "«Привет, как дела?» — два слова, после которых нужен адвокат по репутации.",
        "Ночной романтик. Утром — свидетель обвинения.",
    ),
    "contacts": (
        "Третье голосовое на 9 минут: друзья уже оформляют отпуск от общения.",
        "«Брат, слушай, я щас всё объясню». 04:37. Все всё поняли.",
        "Когда сделал 27 звонков и наконец понял смысл режима «Не беспокоить».",
    ),
    "money": (
        "Открыл банк утром. Хоррор был снят без спецэффектов.",
        "Планировал инвестировать в себя. Инвестировал в урок финансовой грамотности.",
        "Карта: денег нет. Мозг: зато сюжет неплохой.",
    ),
    "travel": (
        "Собрался ехать навстречу судьбе. Судьба просила вызвать такси домой.",
        "В голове «Форсаж». На деле нужен плед и выключенный навигатор.",
        "Самый опасный квест ночи — найти ключи и решить, что можешь ехать. Не можешь.",
    ),
    "sleep": (
        "У тебя в голове конференция на 300 участников. Все забыли, что завтра понедельник.",
        "04:48. Мозг обсуждает устройство вселенной. Будильник: через два часа.",
        "Сон покинул чат. Завтра пришлёт официальный счёт.",
    ),
    "paranoia": (
        "Когда подозреваешь весь район, а подозревать надо свои решения.",
        "Теория заговора: мир против тебя. Реальность: тебе нужен покой.",
        "Головной офис паранойи работает круглосуточно. Пора закрывать филиал.",
    ),
    "check": (
        "Тебя тянет творить херню. Бот пока держит табличку «НЕ НАДО».",
        "Драма отменяется. Сценарист не выспался.",
    ),
    "general": (
        "Сценарий был «чуть погуляю». Получился сериал без спонсоров.",
        "Главный враг сегодняшней ночи — кнопка «ну ещё один раз».",
        "Всё было под контролем. Контроль вышел из чата.",
    ),
    "after": (
        "Утром проснулся главный герой. Сценарист уже уволился.",
        "Ночной я: легенда. Утренний я: заявление об увольнении.",
    ),
    "ok": ("Сегодня ничего не натворил. Невероятный сюжетный поворот.",),
}
SUBSTANCE_EXTRAS = {
    "meph": ("Меф: «Есть идея». Кошелёк: «Иди нахер».",
             "Меф в 03:00: гениальный план. Утром: а можно вернуть репутацию?"),
    "alpha": ("Соль в голове: «Я всё понял». Реальность: ты не нашёл зарядку.",),
    "stimulant": ("Скорость мыслей: 300 км/ч. Скорость здравого смысла: пешеход.",),
    "mdma": ("Вчера любил вообще всех. Сегодня хочется любить только тишину.",),
    "weed": ("Вечер начинался с фильма. Закончилось обсуждением заставки.",),
    "alcohol": ("«Нормально же сидели» — заголовок самого длинного объяснения утром.",),
    "opioid": ("Этот мем лучше отложить. Безопасность важнее приколов.",),
}


def candidates(substance, risk, assets=ASSETS):
    """Rank by specificity; do not mix e.g. alcohol content with mephedrone."""
    sub = SUB_KEYS.get(substance, "general")
    risk = risk if risk in RISK_KEYS else "general"
    paths = [
        Path(assets) / "contextual" / sub / risk,
        Path(assets) / "contextual" / sub / "general",
        Path(assets) / "contextual" / "general" / risk,
    ]
    if risk in LEGACY_CATEGORIES:
        paths.append(Path(assets) / LEGACY_CATEGORIES[risk])
    if risk in ("general", "check", "ok"):
        paths.append(Path(assets) / "general")
    for directory in paths:
        if not directory.is_dir():
            continue
        files = [p for p in directory.iterdir()
                 if p.is_file() and p.suffix.lower() in IMAGES
                 and not p.name.startswith("original_") and p.stat().st_size > 0]
        if files:
            return files
    return []


def joke(substance, risk):
    risk = risk if risk in TEXT_JOKES else "general"
    sub = SUB_KEYS.get(substance, "general")
    if risk == "general" and sub in SUBSTANCE_EXTRAS:
        return random.choice(SUBSTANCE_EXTRAS[sub])
    return random.choice(TEXT_JOKES[risk])


def import_admin_photo(photo_bytes, substance_key, risk_key, admin_id, assets=ASSETS):
    """Import an owner-approved image, strip EXIF and record provenance."""
    allowed_subs = set(SUB_KEYS.values())
    if substance_key not in allowed_subs or risk_key not in RISK_KEYS:
        raise ValueError("Invalid meme tags")
    if not photo_bytes or len(photo_bytes) > 8_000_000:
        raise ValueError("Missing or oversized photo")
    try:
        with Image.open(io.BytesIO(photo_bytes)) as original:
            original.verify()
        with Image.open(io.BytesIO(photo_bytes)) as original:
            if original.width < 300 or original.height < 180 or original.width * original.height > 20_000_000:
                raise ValueError("Photo dimensions not supported")
            clean = ImageOps.exif_transpose(original).convert("RGB")
            clean.thumbnail((1600, 1600))
            output = io.BytesIO()
            clean.save(output, "JPEG", quality=85, optimize=True)
            data = output.getvalue()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("Invalid image") from exc
    sha = hashlib.sha256(data).hexdigest()
    dest = Path(assets) / "contextual" / substance_key / risk_key / (sha + ".jpg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        temp = dest.with_name(dest.name + ".tmp")
        try:
            temp.write_bytes(data)
            os.replace(temp, dest)
        finally:
            temp.unlink(missing_ok=True)
    # Store immutable confirmation of administrator rights statement.
    def append(rows):
        if not any(item.get("sha256") == sha for item in rows):
            rows.append({
                "sha256": sha, "file": str(dest.relative_to(Path(assets))),
                "admin_id": int(admin_id), "approved_at": datetime.now(timezone.utc).isoformat(),
                "rights_confirmed_by_admin": True, "substance": substance_key, "risk": risk_key,
            })
    transact(append, ROOT / "data" / "meme_admin_imports.json")
    return dest


async def send(message, uid, substance, risk, connect, meme_key, record_meme, meme_buttons):
    """Send one suitable meme immediately, or an honest brief text-only joke."""
    options = candidates(substance, risk)
    if options:
        with connect() as c:
            recent = {r[0] for r in c.execute(
                "SELECT meme_key FROM meme_sends WHERE user_id=? ORDER BY id DESC LIMIT 25",
                (uid,)).fetchall()}
            disliked = {r[0] for r in c.execute(
                """SELECT meme_key FROM meme_votes GROUP BY meme_key
                   HAVING COUNT(*) >= 4 AND SUM(CASE WHEN vote=-1 THEN 1 ELSE 0 END)
                   * 1.0 / COUNT(*) >= .75"""
            ).fetchall()}
        options = [p for p in options if meme_key(p) not in disliked]
        fresh = [p for p in options if meme_key(p) not in recent]
        chosen = random.choice(fresh or options) if options else None
        if chosen is not None:
            await message.answer_photo(
                FSInputFile(chosen),
                caption=f"🎭 {substance} • {risk} | без нравоучений",
                reply_markup=meme_buttons(chosen),
            )
            record_meme(uid, chosen)
            return True
    await message.answer("🎭 " + joke(substance, risk))
    return False
