import asyncio
import io
import os
import hashlib
import random
import sqlite3
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, BufferedInputFile
from dotenv import load_dotenv
from meme_studio import ensure_catalog
import context_memes
import v2_data
import v2_experience

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DB = Path(__file__).with_name("tracker.db")
PRESETS = [
    "Не звонить бывшей", "Не названивать друзьям",
    "Не ехать за приключениями", "Не тратить лишние деньги",
    "Не садиться за руль", "Не писать лишнего",
    "Не смешивать вещества", "Не оставаться одному, если плохо",
    "Не покупать ещё", "Не выкладывать лишнего", "Не ехать к незнакомым людям",
]
SUBSTANCES = ["Мефедрон", "Альфа-ПВП", "Амфетамин", "Метамфетамин",
              "Каннабис", "MDMA", "Кокаин", "Опиоиды",
              "Бензодиазепины", "ЛСД", "Псилоцибин", "Другое", "Не указывать"]
INTERVALS = (10, 15, 30, 60)
dp = Dispatcher()


class PrivateChatOnly(BaseMiddleware):
    """Do not expose health/substance data in Telegram groups or channels."""
    async def __call__(self, handler, event, data):
        chat = getattr(event, "chat", None)
        if chat is None:
            chat = getattr(getattr(event, "message", None), "chat", None)
        if chat is not None and chat.type != "private":
            return None
        return await handler(event, data)


dp.message.outer_middleware(PrivateChatOnly())
dp.callback_query.outer_middleware(PrivateChatOnly())


class Form(StatesGroup):
    custom = State()


def connect():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=20000")
    return c


def utcnow():
    return datetime.now(timezone.utc)


def iso():
    return utcnow().isoformat(timespec="seconds")


def init():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, substance TEXT);
        CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, substance TEXT NOT NULL, started TEXT NOT NULL, ended TEXT);
        CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, at TEXT NOT NULL, note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS goals (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, title TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS checklist (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, selected INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS alerts (user_id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, interval_min INTEGER NOT NULL DEFAULT 30, next_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id,started);
        CREATE TABLE IF NOT EXISTS meme_sends (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          meme_key TEXT NOT NULL,
          at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_meme_sends_user ON meme_sends(user_id, id DESC);
        CREATE TABLE IF NOT EXISTS meme_votes (
          user_id INTEGER NOT NULL, meme_key TEXT NOT NULL,
          vote INTEGER NOT NULL CHECK(vote IN (-1, 1)),
          PRIMARY KEY(user_id, meme_key)
        );
        """)
    v2_data.migrate(connect)


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


def active(c, uid):
    return c.execute("SELECT * FROM sessions WHERE user_id=? AND ended IS NULL ORDER BY id DESC LIMIT 1", (uid,)).fetchone()


def ensure_preset(c, uid):
    """Append new optional defaults without resetting any old/custom choices."""
    existing = {row[0] for row in c.execute(
        "SELECT title FROM checklist WHERE user_id=?", (uid,)
    ).fetchall()}
    missing = [(uid, title) for title in PRESETS if title not in existing]
    if missing:
        c.executemany("INSERT INTO checklist(user_id,title,selected) VALUES(?,?,0)", missing)


def picked(c, uid):
    return c.execute("SELECT * FROM checklist WHERE user_id=? AND selected=1 ORDER BY id", (uid,)).fetchall()


async def display(message, text, markup):
    try:
        if message.text is not None:
            await message.edit_text(text, reply_markup=markup)
            return
        await message.delete()
    except Exception:
        pass
    await message.answer(text, reply_markup=markup)


def home_buttons(running=False):
    rows = [
        [("🫀 Как я?", "v2:checkin"), ("🆘 Мне плохо", "v2:safety")],
        [("🔥 Останови меня", "v2:impulses")],
        [("☑️ Мои запреты", "check"), ("🎭 Мем", "v2:meme")],
        [("📊 Моя статистика", "v2:stats"), ("❓ Вопросы", "v2:faq")],
        [("⚙️ Напоминания", "v2:settings")],
    ]
    if running:
        rows.insert(0, [("🔴 Сопровождение активно", "v2:settings"),
                        ("⏹ Завершить", "v2:finish")])
    else:
        rows.insert(0, [("▶️ Начать сопровождение", "v2:start")])
    return kb(rows)


async def home(message, uid):
    with connect() as c:
        session = active(c, uid)
        ensure_preset(c, uid)
        count = len(picked(c, uid))
    if session:
        text = f"💀 НЕ НАТВОРИ ДЕЛОВ\n\n🔴 Сопровождение активно.\nТвоих запретов: {count}\n\nЕсли тебя несёт или плохо — есть быстрые кнопки."
    else:
        text = "💀 НЕ НАТВОРИ ДЕЛОВ\n\nНачни сопровождение, отметь свои запреты или просто проверь состояние.\nБез длинных анкет."
    await display(message, text, home_buttons(bool(session)))


def meme_key(path):
    relative = Path(path).resolve().relative_to(Path(__file__).resolve().parent)
    return hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()[:16]


def meme_buttons(path):
    key = meme_key(path)
    return kb([[("😂 Зашло", f"mv:1:{key}"), ("🥱 Не смешно", f"mv:-1:{key}")],
               [("🎲 Ещё мем", "mnext")]])


def record_meme(uid, path):
    key = meme_key(path)
    with connect() as c:
        c.execute("INSERT INTO meme_sends(user_id,meme_key,at) VALUES(?,?,?)", (uid, key, iso()))
        c.execute("""DELETE FROM meme_sends WHERE user_id=? AND id NOT IN
                     (SELECT id FROM meme_sends WHERE user_id=? ORDER BY id DESC LIMIT 80)""",
                  (uid, uid))


async def deliver_meme(message, uid, category="general"):
    """Backwards-compatible photo-only API: no text-only joke fallback."""
    map_risk = {"ex": "ex", "calls": "contacts", "money": "money",
                "travel": "travel", "general": "general"}
    with connect() as c:
        session = active(c, uid)
        settings = v2_data.get_settings(c, uid)
        substance = session["substance"] if session else settings["last_substance"]
    return await context_memes.send(message, uid, substance,
                                    map_risk.get(category, "general"), connect,
                                    meme_key, record_meme, meme_buttons)


async def send_situation_meme(message, uid):
    """Approved old-school photo if context matches, not the old quote-card generator."""
    with connect() as c:
        session = active(c, uid)
        settings = v2_data.get_settings(c, uid)
        substance = session["substance"] if session else settings["last_substance"]
        titles = [x["title"] for x in picked(c, uid)]
        risk_row = c.execute(
            "SELECT tag FROM v2_events WHERE user_id=? AND event_type='risk' "
            "AND created_at>=? ORDER BY id DESC LIMIT 1",
            (uid, v2_data.stamp(utcnow() - timedelta(hours=3)))
        ).fetchone()
        risk = risk_row["tag"] if risk_row else v2_experience.risk_from_titles(titles)
    return await context_memes.send(message, uid, substance, risk, connect,
                                    meme_key, record_meme, meme_buttons)


@dp.message(Command("meme"))
async def send_test_meme(m: Message):
    await send_situation_meme(m, m.from_user.id)


@dp.callback_query(F.data == "mnext")
async def meme_next(q: CallbackQuery):
    await q.answer()
    await send_situation_meme(q.message, q.from_user.id)


@dp.callback_query(F.data.startswith("mv:"))
async def meme_vote(q: CallbackQuery):
    try:
        _, value, key = q.data.split(":")
        vote = int(value)
        if vote not in (-1, 1) or len(key) != 16 or any(ch not in "0123456789abcdef" for ch in key):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        return await q.answer("Неверная оценка", show_alert=True)
    with connect() as c:
        shown = c.execute("SELECT 1 FROM meme_sends WHERE user_id=? AND meme_key=? LIMIT 1",
                          (q.from_user.id, key)).fetchone()
        if not shown:
            return await q.answer("Этот мем тебе не отправляли", show_alert=True)
        c.execute("""INSERT INTO meme_votes(user_id,meme_key,vote) VALUES(?,?,?)
                     ON CONFLICT(user_id,meme_key) DO UPDATE SET vote=excluded.vote""",
                  (q.from_user.id, key, vote))
    await q.answer("😂 Учту!" if vote == 1 else "🥱 Учту, покажу другое!")


@dp.message(Command("meme_status"))
async def meme_status(m: Message):
    admins = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
    if not m.from_user or m.from_user.id not in admins:
        return
    from meme_review_store import read as read_review
    root = Path(__file__).resolve().parent / "assets" / "memes"
    categories = ("general", "ex", "calls", "money", "travel")
    original_count = curated_count = 0
    for category in categories:
        folder = root / category
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.is_file() and path.suffix.lower() in (".jpg", ".png", ".jpeg", ".webp"):
                if path.name.startswith("original_"):
                    original_count += 1
                else:
                    curated_count += 1
    try:
        pending = sum(x.get("status") == "needs-rights-review" for x in read_review())
    except (ValueError, OSError):
        pending = -1
    with connect() as c:
        rated = c.execute("SELECT COUNT(*) FROM meme_votes").fetchone()[0]
        sent = c.execute("SELECT COUNT(*) FROM meme_sends").fetchone()[0]
    ai_enabled = os.getenv("MEME_AI_AUTOGEN", "0") == "1" and bool(os.getenv("OPENAI_API_KEY"))
    await m.answer(f"🎭 МЕМ-СТУДИЯ\n\nОригинальные картинки: {original_count}\n"
                   f"Одобренные чужие: {curated_count}\nНа проверке: {pending}\n"
                   f"Запомненных показов: {sent}\nОценок: {rated}\n"
                   f"AI-автор: {'включён' if ai_enabled else 'выключен'}\n"
                   f"AI-проверка изображений: {'включена' if os.getenv('MEME_AI_VISION', '0') == '1' and os.getenv('OPENAI_API_KEY') else 'выключена'}\n"
                   f"Поиск источников: {'включён' if os.getenv('MEME_AUTO_DISCOVERY', '1') == '1' else 'выключен'}")


@dp.message(Command("meme_queue"))
async def meme_queue(m: Message):
    """Admin-only queue: third-party images never auto-publish without rights."""
    admins = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
    if not m.from_user or m.from_user.id not in admins:
        return
    from meme_review_store import read as read_review
    from meme_harvester import ROOT
    from aiogram.types import FSInputFile
    try:
        pending = [x for x in read_review() if x.get("status") == "needs-rights-review"]
    except (OSError, ValueError) as exc:
        return await m.answer(f"Очередь повреждена: {type(exc).__name__}. Проверь файл на сервере.")
    pending.sort(key=lambda item: (-(item.get("vision_humor") or 0), -int(item.get("relevance", 0)), item.get("id", 0)))
    if not pending:
        return await m.answer("Очередь пуста. Оригинальные мемы публикуются автоматически.")
    for item in pending[:5]:
        path = (ROOT / item["preview"]).resolve()
        if path.is_file() and path.is_relative_to((ROOT / "data" / "meme_review").resolve()):
            await m.answer_photo(FSInputFile(path),
                caption=f"#{item['id']} • Проверь, что мем по теме, смешной и у тебя есть права. Источник: {item['source']}"[:1024],
                reply_markup=kb([[("✅ Права и содержание проверены", f"mapprove:{item['id']}"),
                                  ("❌ Не подходит", f"mreject:{item['id']}")]]))
    await m.answer(f"Ждут проверки: {len(pending)}. Показаны первые 5.")


@dp.callback_query(F.data.startswith("mapprove:") | F.data.startswith("mreject:"))
async def review_meme(q: CallbackQuery):
    admins = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
    if q.from_user.id not in admins:
        return await q.answer("Нет доступа", show_alert=True)
    import shutil
    from meme_review_store import transact
    from meme_harvester import ROOT
    try:
        item_id = int(q.data.partition(":")[2])
    except ValueError:
        return await q.answer("Неверный ID")
    def update(items):
        item = next((x for x in items
                     if x.get("id") == item_id and x.get("status") == "needs-rights-review"), None)
        if not item:
            return False
        if q.data.startswith("mapprove:"):
            source = (ROOT / item["preview"]).resolve()
            directory = (ROOT / "data" / "meme_review").resolve()
            if not source.is_file() or not source.is_relative_to(directory):
                raise ValueError("Исходный файл отсутствует")
            target_dir = ROOT / "assets" / "memes" / "general"
            target_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            target = target_dir / (digest + ".jpg")
            if not target.exists():
                shutil.copyfile(source, target)
            item["status"] = "approved"
            item["file"] = str(target.relative_to(ROOT))
        else:
            item["status"] = "rejected"
        return True
    try:
        updated = transact(update)
    except (OSError, ValueError) as exc:
        return await q.answer(f"Ошибка: {type(exc).__name__}", show_alert=True)
    if not updated:
        return await q.answer("Этот мем уже обработан", show_alert=True)
    await q.answer("Сохранено")
    await q.message.edit_reply_markup(reply_markup=None)


@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await home(m, m.from_user.id)


@dp.callback_query(F.data == "home")
async def to_home(q: CallbackQuery):
    await q.answer()
    await home(q.message, q.from_user.id)


async def checklist(message, uid):
    with connect() as c:
        ensure_preset(c, uid)
        rows = c.execute("SELECT * FROM checklist WHERE user_id=? ORDER BY id", (uid,)).fetchall()
        session = active(c, uid)
    markup = [[(("✅ " if r["selected"] else "▫️ ") + r["title"][:55], f"toggle:{r['id']}")] for r in rows]
    markup += [[("✍️ Свой пункт", "custom")]]
    markup += [[("▶️ Начать" if not session else "🔔 Настроить сопровождение", "v2:start" if not session else "v2:settings")]]
    markup += [[("🏠 Назад", "home")]]
    await display(message, "☑️ Я НЕ ХОЧУ\n\nНажми на то, чего сегодня НЕ хочешь делать. Повторное нажатие убирает галочку.", kb(markup))


@dp.callback_query(F.data == "check")
async def check(q: CallbackQuery):
    await q.answer()
    await checklist(q.message, q.from_user.id)


@dp.callback_query(F.data.startswith("toggle:"))
async def toggle(q: CallbackQuery):
    with connect() as c:
        item = c.execute("SELECT id FROM checklist WHERE id=? AND user_id=?", (q.data.split(":")[1], q.from_user.id)).fetchone()
        if item:
            c.execute("UPDATE checklist SET selected=1-selected WHERE id=?", (item["id"],))
    await q.answer()
    await checklist(q.message, q.from_user.id)


@dp.callback_query(F.data == "custom")
async def custom(q: CallbackQuery, state: FSMContext):
    await q.answer()
    await state.set_state(Form.custom)
    await display(q.message, "✍️ Напиши одним сообщением, чего не хочешь делать.\n\nНапример: «Не брать кредиты».\n/start — отмена.", None)


@dp.message(Form.custom)
async def save_custom(m: Message, state: FSMContext):
    if not m.text or not m.text.strip() or len(m.text.strip()) > 100:
        return await m.answer("Отправь текст до 100 символов.")
    with connect() as c:
        c.execute("INSERT INTO checklist(user_id,title,selected) VALUES(?,?,1)", (m.from_user.id, m.text.strip()))
    await state.clear()
    await m.answer("✅ Добавлено.", reply_markup=kb([[("☑️ К чек-листу", "check")]]))


@dp.callback_query(F.data == "substance")
async def substance(q: CallbackQuery):
    with connect() as c:
        session = active(c, q.from_user.id)
        selected = picked(c, q.from_user.id)
    await q.answer()
    if session:
        return await home(q.message, q.from_user.id)
    if not selected:
        return await display(q.message, "Отметь хотя бы один пункт.", kb([[("☑️ К списку", "check")]]))
    rows = [[(name, f"sub:{i}")] for i, name in enumerate(SUBSTANCES)]
    rows.append([("⬅️ Назад", "check")])
    await display(q.message, "Что употребляешь или планируешь?\nМожно не указывать. Здесь нет дозировок и рекомендаций по употреблению.", kb(rows))


@dp.callback_query(F.data.startswith("sub:"))
async def choose_sub(q: CallbackQuery):
    try:
        idx = int(q.data.partition(":")[2])
        name = SUBSTANCES[idx]
        if idx < 0:
            raise IndexError()
    except (ValueError, IndexError):
        return await q.answer("Неизвестный пункт", show_alert=True)
    with connect() as c:
        session = active(c, q.from_user.id)
        if not session:
            cur = c.execute("INSERT INTO sessions(user_id,substance,started) VALUES(?,?,?)",
                            (q.from_user.id, name, iso()))
            c.execute("INSERT INTO users(user_id,substance) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET substance=excluded.substance",
                      (q.from_user.id, name))
            sid = cur.lastrowid
        else:
            sid = session["id"]
    await q.answer()
    await display(q.message, "🔔 Как часто напоминать о твоих решениях?", kb([
        [("15 минут", f"interval:{sid}:15"), ("30 минут", f"interval:{sid}:30")],
        [("60 минут", f"interval:{sid}:60")],
        [("⏹ Без напоминаний", "finish")]
    ]))


@dp.callback_query(F.data.startswith("interval:"))
async def interval(q: CallbackQuery):
    try:
        _, sid_str, minutes_str = q.data.split(":")
        sid, minutes = int(sid_str), int(minutes_str)
    except (ValueError, TypeError):
        return await q.answer("Неверная настройка", show_alert=True)
    if minutes not in INTERVALS:
        return await q.answer("Неверная частота", show_alert=True)
    with connect() as c:
        ses = active(c, q.from_user.id)
        if not ses or ses["id"] != sid:
            return await q.answer("Сессия уже завершена", show_alert=True)
        next_at = (utcnow() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        c.execute("""INSERT INTO alerts(user_id,session_id,interval_min,next_at) VALUES(?,?,?,?)
                    ON CONFLICT(user_id) DO UPDATE SET session_id=excluded.session_id,
                    interval_min=excluded.interval_min,next_at=excluded.next_at""",
                  (q.from_user.id, sid, minutes, next_at))
    await q.answer("Включено")
    await home(q.message, q.from_user.id)


@dp.callback_query(F.data == "finish")
async def finish(q: CallbackQuery):
    with connect() as c:
        ses = active(c, q.from_user.id)
        if ses:
            c.execute("UPDATE sessions SET ended=? WHERE id=?", (iso(), ses["id"]))
        c.execute("DELETE FROM alerts WHERE user_id=?", (q.from_user.id,))
    await q.answer("Завершено")
    await home(q.message, q.from_user.id)


@dp.callback_query(F.data == "health")
async def health(q: CallbackQuery):
    await q.answer()
    await display(q.message, "🫀 КАК ТЫ?\n\nЕсли сильная боль в груди, судороги, потеря сознания, проблемы с дыханием, перегрев или сильная спутанность — вызывай экстренную помощь (112 в России).\n\nНе садись за руль. При ухудшении попроси трезвого человека быть рядом. Бот не заменяет врачей.", kb([
        [("👌 Нормально", "home"), ("🆘 Мне плохо", "safety")],
        [("🏠 Назад", "home")]
    ]))


@dp.callback_query(F.data == "safety")
async def safety(q: CallbackQuery):
    await q.answer()
    await display(q.message, "🆘 ЕСЛИ ПЛОХО\n\nПри боли в груди, судорогах, потере сознания, проблемах с дыханием, перегреве или выраженной спутанности — сразу звони 112 (Россия) или в местную экстренную службу.\n\nНе оставайся один. Если человек без сознания, но дышит, поверни его на бок. Если не дышит нормально — следуй указаниям диспетчера.", kb([[("🏠 Меню", "home")]]))


def month_data(c, uid):
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = c.execute("SELECT substance,started,ended FROM sessions WHERE user_id=? AND started>=? ORDER BY started",
                     (uid, start.isoformat())).fetchall()
    weeks = [0] * 5
    substances = Counter()
    durations = []
    for row in rows:
        when = datetime.fromisoformat(row["started"])
        weeks[min(4, (when.day - 1)//7)] += 1
        substances[row["substance"]] += 1
        if row["ended"]:
            try:
                hours = (datetime.fromisoformat(row["ended"]) - when).total_seconds()/3600
                if hours >= 0:
                    durations.append(hours)
            except (ValueError, TypeError):
                pass
    return rows, weeks, substances, durations


@dp.callback_query(F.data == "stats")
async def stats(q: CallbackQuery):
    """Legacy button compatibility: show the new image instead of ASCII bars."""
    await q.answer()
    with connect() as c:
        summary = v2_data.monthly_summary(c, q.from_user.id)
    try:
        from stats_dashboard import make_dashboard, summary_caption
        image = await asyncio.to_thread(make_dashboard, summary)
        await q.message.answer_photo(
            BufferedInputFile(image, filename="private-stats.jpg"),
            caption=summary_caption(summary),
            reply_markup=kb([
                [("📊 Обновить", "v2:stats"), ("🏠 Меню", "v2:home")],
                [("🗑 Удалить мои данные", "delete_ask")],
            ]),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Legacy stats image failed: {type(exc).__name__}")
        await q.message.answer(summary_caption(summary))


@dp.callback_query(F.data == "more")
async def more(q: CallbackQuery):
    await q.answer()
    await display(q.message, "⚙️ ЕЩЁ\n\nБот хранит твои отметки на сервере. Вход в бота — только через личный чат. Удаление из базы не удаляет резервные копии сервера.", kb([
        [("🛟 Безопасность", "safety")],
        [("🗑 Удалить мои данные", "delete_ask")],
        [("🏠 Меню", "home")]
    ]))


@dp.callback_query(F.data == "delete_ask")
async def delete_ask(q: CallbackQuery):
    await q.answer()
    await display(q.message, "🗑 Удалить всю историю, чек-лист и напоминания?\n\nЭто нельзя отменить.", kb([
        [("Да, удалить", "delete_yes")], [("Нет", "home")]
    ]))


@dp.callback_query(F.data == "delete_yes")
async def delete_yes(q: CallbackQuery):
    uid = q.from_user.id
    with connect() as c:
        c.execute("DELETE FROM alerts WHERE user_id=?", (uid,))
        c.execute("DELETE FROM checklist WHERE user_id=?", (uid,))
        c.execute("DELETE FROM entries WHERE session_id IN (SELECT id FROM sessions WHERE user_id=?)", (uid,))
        c.execute("DELETE FROM goals WHERE session_id IN (SELECT id FROM sessions WHERE user_id=?)", (uid,))
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE user_id=?", (uid,))
        c.execute("DELETE FROM meme_sends WHERE user_id=?", (uid,))
        c.execute("DELETE FROM meme_votes WHERE user_id=?", (uid,))
        v2_data.erase_user(c, uid)
    await q.answer("Удалено")
    await display(q.message, "История удалена из активной базы. Резервные копии на сервере удаляются отдельно.", kb([[("🏠 Меню", "home")]]))


def choose_meme(category, previous=None, user_id=None):
    """Prefer a fresh, positively received local image; no unreviewed web files."""
    root = Path(__file__).resolve().parent / "assets" / "memes"
    folder = root / category
    if not folder.is_dir():
        return None
    paths = [x for x in folder.iterdir()
             if x.is_file() and x.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
             and x.stat().st_size > 0]
    if not paths:
        return None
    recent = set()
    blocked = set()
    if user_id is not None:
        with connect() as c:
            recent = {r[0] for r in c.execute(
                "SELECT meme_key FROM meme_sends WHERE user_id=? ORDER BY id DESC LIMIT 25",
                (user_id,)).fetchall()}
            blocked = {r[0] for r in c.execute(
                """SELECT meme_key FROM meme_votes GROUP BY meme_key
                   HAVING COUNT(*) >= 4 AND
                   SUM(CASE WHEN vote=-1 THEN 1 ELSE 0 END)*1.0/COUNT(*) >= 0.75"""
            ).fetchall()}
    allowed = [x for x in paths if meme_key(x) not in blocked and x.name != previous]
    if not allowed:
        return None
    fresh = [x for x in allowed if meme_key(x) not in recent]
    return random.choice(fresh or allowed)


async def reminder_loop(bot):
    """Opt-in reminders: photos every selected interval, retry on delivery failure.

    Do not silently close a session after 24 hours. User explicitly ends it or
    pauses notifications. Keep next_at unchanged until the photo is sent.
    """
    from aiogram.types import FSInputFile
    from photo_memes import ensure_pack
    while True:
        try:
            current = utcnow()
            with connect() as c:
                due = c.execute(
                    "SELECT * FROM alerts WHERE next_at<=? ORDER BY next_at LIMIT 50",
                    (v2_data.stamp(current),)
                ).fetchall()
            for alert in due:
                uid, sid = alert["user_id"], alert["session_id"]
                prev_due = alert["next_at"]
                with connect() as c:
                    session = c.execute(
                        "SELECT * FROM sessions WHERE id=? AND user_id=? AND ended IS NULL",
                        (sid, uid)
                    ).fetchone()
                    if not session:
                        c.execute("DELETE FROM alerts WHERE user_id=? AND session_id=?",
                                  (uid, sid))
                        continue
                    settings = v2_data.get_settings(c, uid)
                    # Preserve legacy reminder intervals, and restore the base
                    # interval after temporary 10-minute boost expires.
                    minutes = max(10, int(alert["interval_min"]))
                    if settings["boost_until"]:
                        minutes = v2_data.active_minutes(settings, current) or minutes
                    if settings["quiet"]:
                        c.execute("UPDATE alerts SET next_at=? WHERE user_id=? AND next_at=?",
                                  (v2_data.stamp(current + timedelta(minutes=1)),
                                   uid, prev_due))
                        continue
                    titles = [x["title"] for x in picked(c, uid)]
                    row = c.execute(
                        "SELECT tag FROM v2_events WHERE user_id=? AND event_type='risk' "
                        "AND created_at>=? ORDER BY id DESC LIMIT 1",
                        (uid, v2_data.stamp(current - timedelta(hours=2)))
                    ).fetchone()
                    risk_tag = row["tag"] if row else None

                caption, risk = v2_experience.reminder_text(session["substance"],
                                                            titles, risk_tag)
                keyboard = v2_experience.reminder_keyboard(kb)
                try:
                    path = context_memes.choose_photo(
                        session["substance"], risk, connect, uid, meme_key)
                    if path is None:
                        try:
                            await asyncio.to_thread(ensure_pack)
                        except Exception as exc:
                            print(f"Reminder disk meme generation failed: "
                                  f"{type(exc).__name__}: {exc}", flush=True)
                        path = context_memes.choose_photo(
                            session["substance"], risk, connect, uid, meme_key)
                    if path is None:
                        from aiogram.types import BufferedInputFile
                        from photo_memes import inline_picture
                        # The process may be unable to write pictures to disk.
                        # An in-memory JPEG still guarantees photo reminders.
                        data = await asyncio.to_thread(inline_picture,
                                                       session["substance"], risk)
                        await bot.send_photo(uid,
                            BufferedInputFile(data, filename="reminder-meme.jpg"),
                            caption=caption[:1024], reply_markup=keyboard)
                    else:
                        try:
                            await bot.send_photo(uid, FSInputFile(path),
                                                 caption=caption[:1024],
                                                 reply_markup=keyboard)
                            record_meme(uid, path)
                        except Exception as stored_exc:
                            print(f"Stored reminder picture failed; trying fresh JPG: "
                                  f"{type(stored_exc).__name__}: {stored_exc}", flush=True)
                            from aiogram.types import BufferedInputFile
                            from photo_memes import inline_picture
                            data = await asyncio.to_thread(inline_picture,
                                                           session["substance"], risk)
                            await bot.send_photo(uid,
                                BufferedInputFile(data, filename="reminder-meme.jpg"),
                                caption=caption[:1024], reply_markup=keyboard)
                except Exception as exc:
                    # Leave an audit trail and try again in one minute, rather
                    # than advancing the timer without delivering anything.
                    print(f"Reminder photo failed for {uid}: {type(exc).__name__}: {exc}",
                          flush=True)
                    with connect() as c:
                        c.execute(
                            "UPDATE alerts SET next_at=? WHERE user_id=? "
                            "AND session_id=? AND next_at=?",
                            (v2_data.stamp(utcnow() + timedelta(minutes=1)),
                             uid, sid, prev_due)
                        )
                    continue

                with connect() as c:
                    c.execute(
                        "UPDATE alerts SET interval_min=?, next_at=? WHERE user_id=? "
                        "AND session_id=? AND next_at=?",
                        (minutes, v2_data.stamp(utcnow() + timedelta(minutes=minutes)),
                         uid, sid, prev_due)
                    )
        except Exception as exc:
            print(f"Reminder scheduler error: {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(15)


def reminder_status_text(c, uid):
    """Explain actual scheduled state instead of falsely claiming alerts are on."""
    import math
    session = active(c, uid)
    alert = c.execute("SELECT * FROM alerts WHERE user_id=?", (uid,)).fetchone()
    settings = v2_data.get_settings(c, uid)
    if not session:
        return "🔕 Сопровождение не запущено. Сначала выбери вещество и включи таймер."
    if not alert or alert["session_id"] != session["id"]:
        return ("⚠️ Активная сессия есть, но уведомления НЕ запланированы.\n"
                "Нажми «🔔 Запустить 10 минут», чтобы включить их.")
    if settings["quiet"]:
        return "⏸ Напоминания сейчас отключены. Можно включить их в настройках."
    try:
        next_time = datetime.fromisoformat(alert["next_at"])
        if next_time.tzinfo is None:
            next_time = next_time.replace(tzinfo=timezone.utc)
        mins = max(0, math.ceil((next_time - utcnow()).total_seconds() / 60))
        return (f"🔔 Напоминания включены: каждые {alert['interval_min']} мин.\n"
                f"Следующая отправка: примерно через {mins} мин.\n"
                "Бот должен прислать фотографию с мемом и твоими запретами.")
    except (TypeError, ValueError):
        return "⚠️ Неправильная дата следующей отправки. Повтори настройку 10 минут."


@dp.message(Command("reminders"))
async def reminder_status_command(m: Message):
    with connect() as c:
        status = reminder_status_text(c, m.from_user.id)
    await m.answer(status, reply_markup=kb([
        [("🔔 Запустить 10 минут", "v2:activate10"),
         ("📸 Тестовый мем", "v2:photo_test")],
        [("⚙️ Настройки", "v2:settings"), ("🏠 Меню", "v2:home")],
    ]))


async def meme_update_loop():
    """Discover candidates daily; do not publish unlicensed third-party images."""
    if os.getenv("MEME_AUTO_DISCOVERY", "1") != "1":
        return
    await asyncio.sleep(60)
    while True:
        try:
            from meme_harvester import run
            await asyncio.to_thread(run)
        except Exception as exc:
            print(f"Meme discovery error: {type(exc).__name__}: {exc}")
        await asyncio.sleep(24 * 3600)


async def originals_loop():
    """Maintain original image library and optionally create edited AI jokes."""
    try:
        await asyncio.to_thread(ensure_catalog)
        from meme_ai import restore, generate_daily
        await asyncio.to_thread(restore)
    except Exception as exc:
        print(f"Original meme restore error: {type(exc).__name__}: {exc}")
    while True:
        try:
            from meme_ai import generate_daily
            added = await asyncio.to_thread(generate_daily)
            if added:
                print(f"AI original memes added: {added}")
        except Exception as exc:
            print(f"Optional AI meme writing error: {type(exc).__name__}: {exc}")
        await asyncio.sleep(6 * 3600)


v2_experience.install(
    dp, connect=connect, active=active, picked=picked, ensure_preset=ensure_preset,
    kb=kb, display=display, now=utcnow, stamp=iso,
    meme_key=meme_key, record_meme=record_meme, meme_buttons=meme_buttons,
)


async def complete_picture_library():
    """Do not block Telegram polling on external image downloads."""
    from photo_memes import ensure_pack, upgrade_photos
    try:
        files = await asyncio.to_thread(ensure_pack)
        print(f"Offline picture memes in library: {len(files)}", flush=True)
        n = await asyncio.to_thread(upgrade_photos)
        print(f"Licensed-photo picture memes available: {n}", flush=True)
    except Exception as exc:
        print(f"Picture library background build: {type(exc).__name__}: {exc}", flush=True)


async def main():
    if not TOKEN or TOKEN == "put_your_token_here":
        raise RuntimeError("Set BOT_TOKEN in .env")
    init()
    # Generate a usable offline picture-meme library before Telegram starts.
    from photo_memes import ensure_pack, upgrade_photos
    try:
        ready = await asyncio.to_thread(ensure_pack, limit=21)
        print(f"Picture memes ready at startup: {len(ready)}", flush=True)
    except Exception as exc:
        print(f"Picture meme bootstrap failed: {type(exc).__name__}: {exc}", flush=True)
        # A disk write failure is no longer fatal: try in-memory JPEG.
        try:
            from photo_memes import inline_picture
            data = await asyncio.to_thread(inline_picture, "Мефедрон", "more")
            print(f"Memory-only picture fallback ready: {len(data)} bytes", flush=True)
        except Exception as fallback_exc:
            print(f"CRITICAL: Image rendering unavailable: "
                  f"{type(fallback_exc).__name__}: {fallback_exc}", flush=True)
    if os.getenv("MEME_LEGACY_CARDS", "0") == "1":
        try:
            await asyncio.to_thread(ensure_catalog, limit=16)
        except Exception as exc:
            print(f"Legacy meme rendering unavailable: {type(exc).__name__}: {exc}")
    bot = Bot(TOKEN)
    task = asyncio.create_task(reminder_loop(bot))
    discovery_task = asyncio.create_task(meme_update_loop())
    originals_task = (asyncio.create_task(originals_loop())
                      if os.getenv("MEME_LEGACY_CARDS", "0") == "1" else None)
    followups_task = asyncio.create_task(v2_experience.followup_loop(bot, connect, kb))
    visual_task = asyncio.create_task(complete_picture_library())
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        discovery_task.cancel()
        if originals_task:
            originals_task.cancel()
        followups_task.cancel()
        visual_task.cancel()
        await asyncio.gather(task, discovery_task, followups_task, visual_task,
                             *([originals_task] if originals_task else []),
                             return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
