import asyncio
import io
import os
import hashlib
import hmac
import random
import tempfile
import sqlite3
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, BufferedInputFile, BotCommand, InputMediaPhoto, FSInputFile
from dotenv import load_dotenv
from meme_studio import ensure_catalog
import context_memes
import meme_engine
import real_memes
import mellstroy_bank
import reaction_bank
import pirate_quests
import admin_center
import joke_catalog
import v2_data
import v2_experience
import ui_manager

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


def iso(value=None):
    """Format a datetime; defaults to current UTC for legacy callers."""
    return (value if value is not None else utcnow()).isoformat(timespec="seconds")


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
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS bot_admins (user_id INTEGER PRIMARY KEY, created_at TEXT, last_login TEXT);
        CREATE TABLE IF NOT EXISTS bot_user_profiles (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_seen TEXT);
        """)
    v2_data.migrate(connect)
    meme_engine.init(connect)
    real_memes.init(connect)
    # Previous admin uploader labeled every photo as "Админ".
    # Reclassify those previously uploaded frames as vjlink, preserving files.
    try:
        with reaction_bank.db_session() as media_db:
            media_db.execute("UPDATE reactions SET person='vjlink' WHERE person='Админ'")
    except Exception as exc:
        print(f"Legacy admin collection migration skipped: {exc}", flush=True)


def kb(rows):
    """Native Bot API 10.3 button styling. No extra keyboard menus."""
    def button(label, data):
        style = None
        if data in {"v2:emergency", "delete_yes"}:
            style = "danger"
        elif data in {"v2:finish", "v2:start"}:
            style = "primary"
        elif data in {"v2:home", "home"}:
            style = "success"
        return InlineKeyboardButton(text=label, callback_data=data, style=style)
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(label, data) for label, data in row] for row in rows
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
    return await ui_manager.render_text(message, text, markup)


def home_buttons(running=False, admin=False):
    # Compact public menu. The private harbor remains command-only.
    rows = [
        [("⏹ Закончить, брат" if running else "💀 Сопровождение", "v2:finish" if running else "v2:start")],
        [("☑️ Моё слово", "check"), ("📊 Расклады Маги", "v2:stats")],
        [("😂 Мемчик от Маги", "v2:meme"), ("🆘 Нужна помощь", "v2:safety")],
        [("⚙️ Настройки", "ui:mine")],
    ]
    if admin:
        rows.append([("🛠 Штаб Маги", "admin:panel")])
    return kb(rows)


def is_meme_admin_id(uid):
    if str(uid) in os.getenv("ADMIN_IDS", "").split(","):
        return True
    with connect() as c:
        return c.execute("SELECT 1 FROM bot_admins WHERE user_id=?", (uid,)).fetchone() is not None


class AdminAuth(StatesGroup):
    password = State()
    media = State()
    new_person = State()
    reset_pirate = State()


@dp.message(F.text.regexp(r"(?i)^админ(?:\\s+.+)?$"))
async def admin_enter(m: Message, state: FSMContext):
    if m.chat.type != "private":
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) == 1:
        await state.set_state(AdminAuth.password)
        return await m.answer("🔐 Пароль администратора:")
    await admin_verify(m, state, parts[1])


@dp.message(AdminAuth.password)
async def admin_password(m: Message, state: FSMContext):
    await admin_verify(m, state, m.text or "")


async def admin_verify(m, state, password):
    secret = os.getenv("ADMIN_PASSWORD", "")
    # Limit repeated guesses per Telegram account (15-minute lockout).
    now_ts = int(utcnow().timestamp())
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS admin_login_attempts (
            user_id INTEGER PRIMARY KEY, failures INTEGER NOT NULL,
            locked_until INTEGER NOT NULL DEFAULT 0)""")
        row = c.execute("SELECT failures,locked_until FROM admin_login_attempts WHERE user_id=?",
                        (m.from_user.id,)).fetchone()
    locked = bool(row and row["locked_until"] > now_ts)
    ok = not locked and bool(secret) and hmac.compare_digest(password, secret)
    try:
        await m.delete()
    except Exception:
        pass
    if not ok:
        if locked:
            return await m.answer("🔒 Слишком много попыток. Повтори через 15 минут.")
        failures = (row["failures"] if row and row["locked_until"] <= now_ts else 0) + 1
        until = now_ts + 900 if failures >= 5 else 0
        with connect() as c:
            c.execute("""INSERT INTO admin_login_attempts VALUES(?,?,?)
                         ON CONFLICT(user_id) DO UPDATE SET
                         failures=excluded.failures,locked_until=excluded.locked_until""",
                      (m.from_user.id, failures, until))
        return await m.answer("❌ Неверный пароль или ADMIN_PASSWORD не задан.")
    with connect() as c:
        c.execute("DELETE FROM admin_login_attempts WHERE user_id=?", (m.from_user.id,))
    with connect() as c:
        c.execute("""INSERT INTO bot_admins VALUES(?,?,?)
                     ON CONFLICT(user_id) DO UPDATE SET last_login=excluded.last_login""",
                  (m.from_user.id, iso(), iso()))
    await state.clear()
    await m.answer("✅ Вход выполнен", reply_markup=kb([[("🛠 Штаб Маги", "admin:panel")]]))


@dp.callback_query(F.data == "admin:panel")
async def admin_panel(q: CallbackQuery):
    await q.answer()
    if not is_meme_admin_id(q.from_user.id):
        return
    with connect() as c:
        users = c.execute("SELECT COUNT(*) FROM bot_user_profiles").fetchone()[0]
        admins = c.execute("SELECT COUNT(*) FROM bot_admins").fetchone()[0]
    await display(q.message, f"🛠 ШТАБ МАГИ\n\nПользователей: {users}\nАдминов: {admins}", kb([
        [("👥 Пользователи", "admin:users:0"), ("🏴‍☠️ Тайные точки", "ac:treasures:0")],
        [("😂 Мемный центр", "admin:memehub"), ("📢 Рассылка", "ac:broadcast")],
        [("📊 Аналитика", "ac:stats"), ("🛡 Журнал", "ac:audit")],
        [("🎮 События", "ac:events")],
        [("🚪 Выйти из админки", "admin:logout"), ("🏠 На базу", "v2:home")],
    ]))


@dp.callback_query(F.data.startswith("admin:users:"))
async def admin_users(q: CallbackQuery):
    if not is_meme_admin_id(q.from_user.id):
        return await q.answer("Нет доступа", show_alert=True)
    await q.answer()
    page = max(0, int(q.data.rsplit(":", 1)[-1]))
    with connect() as c:
        total = c.execute("SELECT COUNT(*) FROM bot_user_profiles").fetchone()[0]
        rows = c.execute("SELECT user_id, username, first_name FROM bot_user_profiles ORDER BY last_seen DESC LIMIT 10 OFFSET ?", (page*10,)).fetchall()
    items = [[(f"👤 {(r['first_name'] or r['username'] or 'Пользователь')[:24]} · {r['user_id']}", f"admin:user:{r['user_id']}")] for r in rows]
    nav = []
    if page: nav.append(("⬅️", f"admin:users:{page-1}"))
    if (page+1)*10 < total: nav.append(("➡️", f"admin:users:{page+1}"))
    if nav: items.append(nav)
    items.append([("🛠 Штаб Маги", "admin:panel")])
    await display(q.message, f"👥 ВАЦОКИ В БОТЕ · {total}\nСтраница {page+1}", kb(items))


@dp.callback_query(F.data.startswith("admin:user:"))
async def admin_user_detail(q: CallbackQuery):
    if not is_meme_admin_id(q.from_user.id):
        return await q.answer("Нет доступа", show_alert=True)
    await q.answer()
    uid = int(q.data.rsplit(":", 1)[-1])
    with connect() as c:
        user = c.execute("SELECT user_id, username, first_name FROM bot_user_profiles WHERE user_id=?", (uid,)).fetchone()
    if not user:
        return await q.answer("Пользователь не найден", show_alert=True)
    with pirate_quests.db() as c:
        banned = c.execute("SELECT 1 FROM banned_players WHERE user_id=?", (uid,)).fetchone() is not None
        treasures = c.execute("SELECT title FROM treasures WHERE user_id=? ORDER BY id DESC LIMIT 20", (uid,)).fetchall()
        count = c.execute("SELECT COUNT(*) FROM treasures WHERE user_id=?", (uid,)).fetchone()[0]
    titles = "\n".join("• " + r["title"][:60] for r in treasures) or "Нет сокровищ"
    name = user["first_name"] or user["username"] or "Пользователь"
    await display(q.message, f"👤 {name}\nID: {uid}\nДоступ: {'🚫 заблокирован' if banned else '✅ открыт'}\nСокровищ: {count}\n\n{titles}", kb([
        [(("✅ Разблокировать" if banned else "🚫 Заблокировать"), f"admin:ban_user:{uid}")],
        [("🔑 Сбросить пароль гавани", f"admin:pirate_pick:{uid}")],
        [("⬅️ Пользователи", "admin:users:0")],
    ]))


@dp.callback_query(F.data.startswith("admin:ban_user:"))
async def admin_ban_user(q: CallbackQuery):
    if not is_meme_admin_id(q.from_user.id):
        return await q.answer("Нет доступа", show_alert=True)
    uid = int(q.data.rsplit(":", 1)[-1])
    if uid == q.from_user.id or is_meme_admin_id(uid):
        return await q.answer("Администратора блокировать нельзя", show_alert=True)
    with pirate_quests.db() as c:
        banned = c.execute("SELECT 1 FROM banned_players WHERE user_id=?", (uid,)).fetchone()
        if banned:
            c.execute("DELETE FROM banned_players WHERE user_id=?", (uid,))
        else:
            c.execute("INSERT OR IGNORE INTO banned_players(user_id) VALUES(?)", (uid,))
    await q.answer("Разблокирован" if banned else "Заблокирован")
    await admin_user_detail(q)


@dp.callback_query(F.data.startswith("admin:reset_pirate"))
async def admin_reset_pirate_prompt(q: CallbackQuery, state: FSMContext):
    if not is_meme_admin_id(q.from_user.id):
        return await q.answer("Нет доступа", show_alert=True)
    await q.answer()
    try:
        page = max(0, int(q.data.split(":")[-1]))
    except ValueError:
        page = 0
    with connect() as c:
        total = c.execute("SELECT COUNT(*) FROM bot_user_profiles").fetchone()[0]
        rows = c.execute(
            "SELECT user_id, username, first_name FROM bot_user_profiles ORDER BY last_seen DESC LIMIT 10 OFFSET ?",
            (page * 10,)
        ).fetchall()
    items = [[(f"👤 {(r['first_name'] or r['username'] or 'Пользователь')[:25]} · {r['user_id']}",
               f"admin:pirate_pick:{r['user_id']}")] for r in rows]
    nav = []
    if page > 0: nav.append(("⬅️", f"admin:reset_pirate:{page-1}"))
    if (page + 1) * 10 < total: nav.append(("➡️", f"admin:reset_pirate:{page+1}"))
    if nav: items.append(nav)
    items.append([("🛠 Штаб Маги", "admin:panel")])
    await display(q.message, "🔑 ВЫБЕРИ ПОЛЬЗОВАТЕЛЯ\n\nСбросим его кодовое слово, сохранив сокровища.", kb(items))


@dp.callback_query(F.data.startswith("admin:pirate_pick:"))
async def admin_pirate_pick(q: CallbackQuery):
    if not is_meme_admin_id(q.from_user.id):
        return await q.answer("Нет доступа", show_alert=True)
    uid = int(q.data.rsplit(":", 1)[-1])
    await q.answer()
    await display(q.message, f"🔑 Сбросить пароль гавани пользователя {uid}?\n\nСокровища сохранятся.", kb([
        [("✅ Подтвердить", f"admin:pirate_confirm:{uid}")],
        [("⬅️ Пользователи", "admin:reset_pirate:0")],
    ]))


@dp.callback_query(F.data.startswith("admin:pirate_confirm:"))
async def admin_pirate_confirm(q: CallbackQuery):
    if not is_meme_admin_id(q.from_user.id):
        return await q.answer("Нет доступа", show_alert=True)
    uid = int(q.data.rsplit(":", 1)[-1])
    try:
        with pirate_quests.db() as c:
            changed = c.execute("DELETE FROM players WHERE user_id=?", (uid,)).rowcount
    except Exception as exc:
        print(f"Pirate password reset: {exc}", flush=True)
        return await q.answer("Ошибка базы данных", show_alert=True)
    await q.answer("Кодовое слово сброшено" if changed else "Кодового слова пока нет")
    await display(q.message, "✅ Кодовое слово сброшено. Сокровища сохранены." if changed
                  else "У пользователя пока нет кодового слова.",
                  kb([[("👥 Наши вацоки", "admin:reset_pirate:0")],
                      [("🛠 Штаб Маги", "admin:panel")]]))


@dp.callback_query(F.data == "admin:logout")
async def admin_logout(q: CallbackQuery):
    await q.answer()
    with connect() as c:
        c.execute("DELETE FROM bot_admins WHERE user_id=?", (q.from_user.id,))
    await display(q.message, "Всё, брат, из штаба вышли.", kb([[("🏠 На базу", "v2:home")]]))


def admin_people():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS meme_characters (
            name TEXT PRIMARY KEY, created_at TEXT NOT NULL
        )""")
        names = [row[0] for row in c.execute("SELECT name FROM meme_characters ORDER BY name")]
    try:
        names += list(reaction_bank.status()["people"])
    except Exception:
        pass
    return sorted(set(["Меллстрой", "vjlink"] + names), key=str.casefold)


@dp.callback_query(F.data == "admin:memehub")
async def admin_memehub(q: CallbackQuery):
    await q.answer()
    if not is_meme_admin_id(q.from_user.id):
        return
    await display(q.message,
        "😂 МЕМНЫЙ ЦЕНТР МАГИ\n\n"
        "Кадры, персонажи и проверка мемов — в одном месте.",
        kb([
            [("📁 Коллекции и редактор", "admin:collections")],
            [("🛡 Модерация", "ac:memes")],
            [("📸 Добавить кадры", "admin:media")],
            [("⬅️ Штаб Маги", "admin:panel")],
        ]))


@dp.callback_query(F.data == "admin:collections")
async def admin_collections(q: CallbackQuery):
    await q.answer()
    if not is_meme_admin_id(q.from_user.id):
        return
    try:
        status = await asyncio.to_thread(reaction_bank.status)
        people = status["people"]
    except Exception as exc:
        print(f"Admin collections: {exc}", flush=True)
        people = {}
    try:
        old = await asyncio.to_thread(mellstroy_bank.status)
        old_count = old["count"]
    except Exception:
        old_count = 0
    # Old frames are a separate legacy bank; new uploads are grouped by person.
    lines = [f"🎭 Меллстрой — {old_count + people.get('Меллстрой', 0)}",
             f"📸 vjlink — {people.get('vjlink', 0)}"]
    lines += [f"📁 {name} — {people.get(name, 0)}" for name in
              admin_people() if name not in ("Меллстрой", "vjlink")]
    await display(q.message, "😂 МЕМНЫЙ СКЛАД МАГИ\n\n" + "\n".join(lines), kb([
        [("➕ Добавить фото", "admin:media")],
        [("➕ Новый персонаж", "admin:newperson")],
        [("⬅️ Админка", "admin:panel")],
    ]))


@dp.callback_query(F.data == "admin:newperson")
async def admin_new_person(q: CallbackQuery, state: FSMContext):
    await q.answer()
    if not is_meme_admin_id(q.from_user.id):
        return
    await state.set_state(AdminAuth.new_person)
    await display(q.message, "➕ НОВЫЙ ГЕРОЙ, БРАТ\n\nОтправь имя (до 40 символов).", kb([
        [("⬅️ Коллекции", "admin:collections")],
    ]))


@dp.message(AdminAuth.new_person)
async def admin_save_person(m: Message, state: FSMContext):
    if not is_meme_admin_id(m.from_user.id):
        return
    name = (m.text or "").strip()
    if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
        return await m.answer("Укажи корректное имя до 40 символов.")
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS meme_characters (
            name TEXT PRIMARY KEY, created_at TEXT NOT NULL
        )""")
        c.execute("INSERT OR IGNORE INTO meme_characters VALUES(?,?)", (name, iso()))
    await state.update_data(person=name)
    await state.set_state(AdminAuth.media)
    await m.answer(f"✅ Персонаж «{name}» создан. Теперь отправь фото или видео.",
                   reply_markup=kb([[("📁 Мемные сокровища", "admin:collections")]]))


@dp.callback_query(F.data == "admin:media")
async def admin_media_prompt(q: CallbackQuery, state: FSMContext):
    await q.answer()
    if not is_meme_admin_id(q.from_user.id):
        return
    await state.clear()
    names = admin_people()
    rows = [[(name, "admin:person:" + str(i))] for i, name in enumerate(names)]
    rows += [[("➕ Новый персонаж", "admin:newperson")],
             [("⬅️ Админка", "admin:panel")]]
    await display(q.message, "📸 КОМУ ПОДКИНЕМ МЕМ, ВАЦОК?\n\nВыбери персонажа.", kb(rows))


@dp.callback_query(F.data.startswith("admin:person:"))
async def admin_select_person(q: CallbackQuery, state: FSMContext):
    await q.answer()
    if not is_meme_admin_id(q.from_user.id):
        return
    try:
        index = int(q.data.rsplit(":", 1)[-1])
        person = admin_people()[index]
    except (ValueError, IndexError):
        return
    await state.update_data(person=person)
    await state.set_state(AdminAuth.media)
    await display(q.message, f"📸 Коллекция: {person}\n\nПришли фото или видео, на которое у тебя есть права. /start — отмена.", kb([
        [("⬅️ Выбрать другого", "admin:media")],
    ]))


@dp.message(AdminAuth.media)
async def admin_media_upload(m: Message, state: FSMContext):
    if not is_meme_admin_id(m.from_user.id):
        return
    media = m.photo[-1] if m.photo else m.video
    if not media:
        return await m.answer("Отправь фото или видео.")
    if (media.file_size or 0) > 19_000_000:
        return await m.answer("Максимум 19 МБ.")
    data = await state.get_data()
    person = data.get("person")
    if not person or person not in admin_people():
        return await m.answer("Сначала выбери коллекцию в админке.")
    status = await m.answer("⏳ Добавляю...")
    try:
        with tempfile.TemporaryDirectory(prefix="admin_upload_") as tmp:
            path = Path(tmp) / ("source.jpg" if m.photo else "source.mp4")
            await m.bot.download(media.file_id, destination=path)
            result = await asyncio.to_thread(
                reaction_bank.import_media, path, person=person,
                mood="auto", confirm_rights=True, credit=f"Authorized admin upload: {person}")
        await status.edit_text(
            f"✅ {person}\nНовых кадров: {result['added']}\n"
            f"Всего в медиатеке: {result['total']}\nДубликатов: {result['duplicates']}",
            reply_markup=kb([
                [("➕ Ещё фото", "admin:media")],
                [("📁 Мемные сокровища", "admin:collections")],
                [("🛠 Штаб Маги", "admin:panel")],
            ]))
        try:
            await m.delete()
        except Exception:
            pass
    except Exception as exc:
        print(f"Admin media upload: {exc}", flush=True)
        await status.edit_text("❌ Ошибка загрузки. Проверь журнал сервиса.")


@dp.callback_query(F.data == "ui:mine")
async def mine_menu(q: CallbackQuery):
    await q.answer()
    await display(q.message, "⚙️ НАСТРОЙКИ МАГИ\n\n"
                  "Все твои параметры в одном месте, брат.", kb([
        [("🔔 Напоминания", "v2:settings")],
        [("☑️ Мои правила", "check"), ("📊 Моя статистика", "v2:stats")],
        [("😂 Мемный отдел", "v2:memethemes")],
        [("🔐 Мои данные", "ui:privacy")],
        [("🏠 На базу", "v2:home")],
    ]))


@dp.callback_query(F.data == "ui:privacy")
async def privacy_menu(q: CallbackQuery):
    await q.answer()
    await display(q.message, "🔐 МОИ ДАННЫЕ\n\n"
                  "Записи хранятся на сервере бота. Удаление необратимо.", kb([
        [("🗑 Удалить мои записи", "delete_ask")],
        [("⬅️ К настройкам", "ui:mine")],
    ]))


async def home(message, uid):
    with connect() as c:
        session = active(c, uid)
        ensure_preset(c, uid)
        count = len(picked(c, uid))
    markup = home_buttons(bool(session), is_meme_admin_id(uid))
    status = "🔴 Сопровождение активно" if session else "🟢 Сопровождение выключено"
    caption = (
        "🦅 Мага на связи, брат!\n\n"
        f"{status}\n"
        f"☑️ Моих правил: {count}\n\n"
        "Выбирай, что нужно 👇"
    )
    # Prioritize the user's actual Maga photo over experimental Rich Messages.
    # Keep the image server-local and never require committing private assets.
    photo = Path(__file__).resolve().parent / "assets" / "maga_menu_photo.jpg"
    if photo.is_file():
        return await ui_manager.render_photo(message, photo, caption, markup)
    return await display(message, caption, markup)


def meme_key(path):
    relative = Path(path).resolve().relative_to(Path(__file__).resolve().parent)
    return hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()[:16]


def meme_buttons(path):
    key = meme_key(path)
    return kb([[("😂 Вай, зашло", f"mv:1:{key}"), ("🥱 Не зашло", f"mv:-1:{key}")],
               [("🎲 Ещё прикол, вацок", "mnext")]])


def record_meme(uid, path):
    key = meme_key(path)
    with connect() as c:
        c.execute("INSERT INTO meme_sends(user_id,meme_key,at) VALUES(?,?,?)", (uid, key, iso()))
        c.execute("""DELETE FROM meme_sends WHERE user_id=? AND id NOT IN
                     (SELECT id FROM meme_sends WHERE user_id=? ORDER BY id DESC LIMIT 80)""",
                  (uid, uid))


async def deliver_meme(message, uid, category="general"):
    """Historical entry point, now always returns a freshly rendered image."""
    map_risk = {"ex": "ex", "calls": "contacts", "money": "money",
                "travel": "travel", "general": "general"}
    with connect() as c:
        session = active(c, uid)
        settings = v2_data.get_settings(c, uid)
        substance = session["substance"] if session else settings["last_substance"]
    return await real_memes.send(message, uid, substance,
                                map_risk.get(category, "general"), connect)


async def send_situation_meme(message, uid):
    """Use old-school real photo templates with readable, varied Russian jokes."""
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
    return await real_memes.send(message, uid, substance, risk, connect)


@dp.message(Command("meme_count"))
async def meme_count(m: Message):
    """User-only lifetime sequence of generated images."""
    with connect() as c:
        row = c.execute("SELECT serial FROM meme_dynamic_counter WHERE user_id=?",
                        (m.from_user.id,)).fetchone()
        total = int(row[0]) if row else 0
        recent = c.execute("SELECT COUNT(*) FROM real_meme_sent WHERE user_id=?",
                           (m.from_user.id,)).fetchone()[0]
    await m.answer(f"🎭 Создано фотомемов: {total}.\n"
                   f"В истории последних: {recent}.\n"
                   "Кадры берутся из интернет-шаблонов или твоей подборки, "
                   "а русские подписи меняются.")


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


@dp.message(Command("memes"))
async def memes_library_status(m: Message):
    if not is_meme_admin(m.from_user):
        return
    data = await asyncio.to_thread(reaction_bank.status)
    old = mellstroy_bank.status()["count"]
    people = ", ".join(f"{name}: {count}" for name,count in
                       list(data["people"].items())[:8])
    mood_text = ", ".join(f"{m}: {n}" for m,n in data["moods"].items())
    await m.answer(
        f"📸 БИБЛИОТЕКА ФОТО\n\n"
        f"Люди и реакции: {data['count']} кадров (без лимита)\n"
        f"Старая библиотека Меллстроя: {old}\n"
        f"Коллекции: {people or 'пока нет'}\n"
        f"Метки: {mood_text or 'пока нет'}\n"
        f"Автоматическое распознавание: "
        f"{'включено' if data['vision_enabled'] else 'выключено'}\n"
        f"Однофразовых шуток: {joke_catalog.count()}\n\n"
        "Фото/видео с подписью /memes_add YES Имя laugh\n"
        "Или /memes_add YES Имя для автоматической разметки.\n"
        "Другие команды: /memes_preview, /memes_tag, /memes_vision"
    )


@dp.message(Command("memes_diag"))
async def memes_diagnostics(m: Message):
    """Admin-only local diagnostics; never print API keys or user medical data."""
    if not is_meme_admin(m.from_user):
        return
    details = await asyncio.to_thread(real_memes.source_diagnostics, connect)
    parts = ["🔧 ПРОВЕРКА ФОТОМЕМОВ"]
    for kind, label in (("reaction", "Общая библиотека"),
                        ("mellstroy", "Старые кадры Меллстроя"),
                        ("approved", "Фото администратора")):
        item = details.get(kind, {})
        qty = item.get("available", 0)
        usable = "✅" if item.get("image_ok") else "❌"
        parts.append(f"{usable} {label}: проверено {qty} кандидатов")
        if item.get("error"):
            parts.append(f"   Ошибка: {item['error'][:110]}")
    if "jokes" in details:
        parts.append(f"📝 Подписей: {details['jokes']}")
    if "jokes_error" in details:
        parts.append(f"❌ Подписи: {details['jokes_error'][:110]}")
    parts.append("Для полного отчёта: journalctl -u dependence-tracker -n 70")
    await m.answer("\n".join(parts))


@dp.message(Command("memes_preview"))
async def memes_preview(m: Message):
    if not is_meme_admin(m.from_user):
        return
    from aiogram.types import FSInputFile
    photos = await asyncio.to_thread(reaction_bank.sample, 4)
    if not photos:
        return await m.answer("📷 Библиотека пустая. Пришли фото с подписью "
                              "/memes_add YES Имя")
    for item in photos:
        await m.answer_photo(
            FSInputFile(item["path"]),
            caption=f"Реальный кадр: {item['name']}\n"
                    f"Метка: {item['mood']}\n"
                    f"ID: {item['id'][9:]} (исправить: /memes_tag ID shock)")


@dp.message(Command("memes_tag"))
async def memes_retag_one(m: Message):
    if not is_meme_admin(m.from_user):
        return
    chunks=(m.text or "").split()
    if len(chunks)!=3:
        return await m.answer("Формат: /memes_tag ID laugh\n"
                              "Метки: shock laugh yell smug confused tired "
                              "suspicious angry sad neutral")
    try:
        await asyncio.to_thread(reaction_bank.retag_frame,chunks[1],chunks[2])
        await m.answer(f"✅ Кадр {chunks[1]} теперь помечен как {chunks[2]}.")
    except ValueError as exc:
        await m.answer(f"Ошибка разметки: {exc}")


@dp.message(Command("memes_vision"))
async def memes_auto_retag(m: Message):
    if not is_meme_admin(m.from_user):
        return
    if os.getenv("REACTION_VISION_ENABLED","0")!="1":
        return await m.answer("Авторазметка по изображению не включена. "
                              "В .env нужны REACTION_VISION_ENABLED=1 и "
                              "OPENAI_API_KEY. Анализ оплачивается отдельно.")
    await m.answer("🔍 Проверяю изображения моделью по очереди. "
                   "Исправить результат можно через /memes_tag.")
    result = await asyncio.to_thread(reaction_bank.auto_retag, 12)
    await m.answer(f"✅ Проанализировано: {result.get('tagged',0)}\n"
                   "Следующие 12: /memes_vision")


@dp.message(F.photo, F.caption.startswith("/memes_add"))
@dp.message(F.video, F.caption.startswith("/memes_add"))
@dp.message(F.document, F.caption.startswith("/memes_add"))
async def memes_upload(m: Message):
    """Admin-only upload of photos or videos of ANY owner-labeled person.

    No hard frame quota. Telegram-hosted Bot API downloads are capped at 20 MB;
    larger rights-cleared videos can be transferred straight to VPS.
    """
    if not is_meme_admin(m.from_user):
        return
    import shlex
    try:
        pieces = shlex.split(m.caption or "")
    except ValueError:
        return await m.answer("Проверь кавычки в подписи: "
                              "/memes_add YES «Имя» laugh")
    if len(pieces)<2 or pieces[0]!="/memes_add" or pieces[1]!="YES":
        return await m.answer("Подпись: /memes_add YES Имя laugh\n"
                              "YES означает, что у тебя есть право "
                              "использовать загружаемые фотографии/видео.")
    opts = pieces[2:]
    mood = "auto"
    if opts and opts[-1] in reaction_bank.MOODS:
        mood = opts.pop()
    person = " ".join(opts).strip() or "без имени"

    file = m.photo[-1] if m.photo else m.video or m.document
    if not file:
        return
    file_size=getattr(file,"file_size",0) or 0
    if file_size > 19_000_000:
        return await m.answer("Этот файл слишком большой для стандартного "
                              "Telegram Bot API (максимум загрузки ботом ~20 МБ). "
                              "Большой ролик можно импортировать на VPS.")
    extension=(".jpg" if m.photo else
               (".mp4" if m.video else
                Path(getattr(m.document,"file_name","") or "").suffix.lower()))
    if extension not in reaction_bank.PHOTOS|reaction_bank.VIDEOS:
        return await m.answer("Пришли JPEG/PNG/WebP или MP4/MOV/MKV/WEBM.")
    progress=await m.answer("📥 Получил, проверяю кадры и убираю дубли...")
    with tempfile.TemporaryDirectory(prefix="reaction_upload_") as tmp:
        source=Path(tmp)/("input"+extension)
        try:
            await m.bot.download(file.file_id,destination=source)
            result=await asyncio.to_thread(
                reaction_bank.import_media,source,
                person=person,mood=mood,confirm_rights=True,
                credit=f"Authorized Telegram admin upload for {person}")
            errors="\n".join(result["errors"][:2])
            vision=("Метки по фото" if mood=="auto" and
                    os.getenv("REACTION_VISION_ENABLED","0")=="1"
                    else "Метка из подписи/имени")
            await progress.edit_text(
                f"✅ Коллекция: {result['person']}\n"
                f"Новых кадров: {result['added']}\n"
                f"Всего в медиатеке: {result['total']} (без лимита)\n"
                f"Дублей: {result['duplicates']}, отсеяно: {result['rejected']}\n"
                f"Разметка: {vision}\n"
                f"{('Предупреждения: '+errors) if errors else ''}\n"
                "Посмотреть: /memes_preview\n"
                "Исправить метку: /memes_tag ID laugh")
        except Exception as exc:
            print(f"Reaction import failed: {type(exc).__name__}: "
                  f"{str(exc)[:180]}",flush=True)
            await progress.edit_text(
                "❌ Не смог обработать материал. Проверь место на диске, "
                "ffmpeg и журнал сервиса.")


def is_meme_admin(user):
    """No privileged actions for ordinary bot members."""
    admins = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",")
              if x.strip().isdigit()}
    return bool(user and (user.id in admins or is_meme_admin_id(user.id)))


@dp.message(Command("mellstroy"))
async def mellstroy_status_cmd(m: Message):
    if not is_meme_admin(m.from_user):
        return
    state = mellstroy_bank.status()
    general = reaction_bank.status()
    new_count = general["people"].get("Меллстрой", 0)
    tags = ", ".join(f"{k}: {v}" for k, v in state["moods"].items())
    await m.answer(
        f"📸 МЕЛЛСТРОЙ — БИБЛИОТЕКА\n\n"
        f"Кадры из старого архива: {state['count']}\n"
        f"Новые кадры: {new_count}\n"
        "Лимита на коллекцию нет.\n"
        f"Метки: {tags or 'пока нет'}\n\n"
        "Как наполнить:\n"
        "1. Скинь короткое видео с подписью /mellstroy_add YES shock "
        "(до 19 МБ, только материал с разрешением на использование).\n"
        "2. Или загрузите большие исходные видео на VPS и выполните:\n"
        'python reaction_bank.py import /путь/к/видео --person "Меллстрой" --confirm-rights\n'
        "3. /mellstroy_preview — увидеть четыре исходника.\n"
        "Лицо не заменяется и не меняется."
    )


@dp.message(Command("mellstroy_preview"))
async def mellstroy_preview_cmd(m: Message):
    if not is_meme_admin(m.from_user):
        return
    entries = mellstroy_bank.templates("general", limit=4)
    if not entries:
        return await m.answer("Пока нет реальных кадров. /mellstroy — инструкция.")
    from aiogram.types import FSInputFile
    for item in entries[:4]:
        await m.answer_photo(FSInputFile(item["path"]),
            caption=f"📸 Оригинальный кадр • метка: {item['mood']}")


@dp.message(F.video, F.caption.startswith("/mellstroy_add"))
@dp.message(F.document, F.caption.startswith("/mellstroy_add"))
async def mellstroy_add_video(m: Message):
    """Import a rights-confirmed short video directly through Telegram.

    Telegram's hosted Bot API limits bot file downloads to 20 MB. For long
    streams, import source media directly on VPS instead.
    """
    if not is_meme_admin(m.from_user):
        return
    chunks = (m.caption or "").split()
    if len(chunks) not in (2, 3) or chunks[0] != "/mellstroy_add" or chunks[1] != "YES":
        return await m.answer("Подпись: /mellstroy_add YES shock\n"
                              "YES подтверждает права на использование видео.")
    mood = chunks[2] if len(chunks) == 3 else "auto"
    if mood not in ("auto",) + mellstroy_bank.MOODS:
        return await m.answer("Метка: shock, laugh, yell, smug, confused, "
                              "tired, suspicious, angry, sad, neutral или auto.")
    file = m.video or m.document
    filename = getattr(file, "file_name", "") or "clip.mp4"
    extension = Path(filename).suffix.lower()
    if extension not in mellstroy_bank.ALLOWED_VIDEO:
        if m.video:
            extension = ".mp4"
        else:
            return await m.answer("Нужно отправить видео MP4/MOV/WEBM.")
    if not file or (file.file_size and file.file_size > 19_000_000):
        return await m.answer("Telegram ограничивает скачивание ботом: "
                              "нужен ролик до 19 МБ. Большие видео загрузи на VPS.")
    progress = await m.answer("📥 Получил видео. Отбираю неповторяющиеся кадры...")
    with tempfile.TemporaryDirectory(prefix="mellstroy_upload_") as folder:
        path = Path(folder) / ("clip" + extension)
        try:
            await m.bot.download(file.file_id, destination=path)
            result = await asyncio.to_thread(reaction_bank.import_media, path,
                    person="Меллстрой", mood=mood, confirm_rights=True,
                    credit="Admin-confirmed stream clip")
            await progress.edit_text(
                f"✅ Добавлено: {result['added']} реальных кадров.\n"
                f"Общая библиотека: {result['total']} (без лимита).\n"
                f"Дубликатов: {result['duplicates']}, отсеяно: {result['rejected']}.\n"
                "Для просмотра: /memes_preview")
        except Exception as exc:
            print(f"Mellstroy video import failed: {type(exc).__name__}: {exc}", flush=True)
            await progress.edit_text("❌ Не получилось разобрать видео. "
                                     "Проверь ffmpeg, размер файла и журнал бота.")


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
    with connect() as c:
        c.execute("""INSERT INTO bot_user_profiles VALUES(?,?,?,?)
                     ON CONFLICT(user_id) DO UPDATE SET
                     username=excluded.username,first_name=excluded.first_name,
                     last_seen=excluded.last_seen""",
                  (m.from_user.id, m.from_user.username, m.from_user.first_name, iso()))
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
    markup += [[("▶️ Погнали, вацок" if not session else "🔔 Настроить, брат", "v2:start" if not session else "v2:settings")]]
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
    await m.answer("🤝 Готово, вацок!", reply_markup=kb([[("☑️ К моим правилам", "check")]]))


@dp.callback_query(F.data == "substance")
async def substance(q: CallbackQuery):
    with connect() as c:
        session = active(c, q.from_user.id)
        selected = picked(c, q.from_user.id)
    await q.answer()
    if session:
        return await home(q.message, q.from_user.id)
    if not selected:
        return await display(q.message, "Э, брат, выбери хотя бы одно правило.", kb([[("☑️ К моим правилам", "check")]]))
    rows = [[(name, f"sub:{i}")] for i, name in enumerate(SUBSTANCES)]
    rows.append([("⬅️ Обратно, брат", "check")])
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
    await display(q.message, "🆘 ЕСЛИ ПЛОХО\n\nПри боли в груди, судорогах, потере сознания, проблемах с дыханием, перегреве или выраженной спутанности — сразу звони 112 (Россия) или в местную экстренную службу.\n\nНе оставайся один. Если человек без сознания, но дышит, поверни его на бок. Если не дышит нормально — следуй указаниям диспетчера.", kb([[("🏠 На базу", "home")]]))


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
                [("📊 Обновить", "v2:stats"), ("🏠 На базу", "v2:home")],
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
        [("🏠 На базу", "home")]
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
        c.execute("DELETE FROM meme_dynamic_history WHERE user_id=?", (uid,))
        c.execute("DELETE FROM meme_dynamic_counter WHERE user_id=?", (uid,))
        c.execute("DELETE FROM real_meme_sent WHERE user_id=?", (uid,))
        c.execute("DELETE FROM joke_sent WHERE user_id=?", (uid,))
        c.execute("DELETE FROM joke_delivery WHERE user_id=?", (uid,))
        v2_data.erase_user(c, uid)
    await q.answer("Удалено")
    await display(q.message, "История удалена из активной базы. Резервные копии на сервере удаляются отдельно.", kb([[("🏠 На базу", "home")]]))


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
                    # Every due reminder gets a fresh generated character, pose,
                    # background, line and unique per-user serial number.
                    await real_memes.send_bot(bot, uid, session["substance"],
                                              risk, connect, caption, keyboard)
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
        return (f"🔔 Напомни, брат включены: каждые {alert['interval_min']} мин.\n"
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
        [("⚙️ Настройки", "v2:settings"), ("🏠 На базу", "v2:home")],
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


pirate_quests.install(dp)
admin_center.install(dp, connect, is_meme_admin_id, pirate_quests)

v2_experience.install(
    dp, connect=connect, active=active, picked=picked, ensure_preset=ensure_preset,
    kb=kb, display=display, now=utcnow, stamp=iso,
    meme_key=meme_key, record_meme=record_meme, meme_buttons=meme_buttons,
    is_admin=is_meme_admin_id, home_screen=home,
    show_photo=ui_manager.render_photo,
)


async def main():
    if not TOKEN or TOKEN == "put_your_token_here":
        raise RuntimeError("Set BOT_TOKEN in .env")
    init()
    print("Photo meme feed enabled (Imgflip, no key required; "
          "optional paid AI only with explicit opt-in)", flush=True)
    bot = Bot(TOKEN)
    # Native Telegram slash-menu is a compact quick entry for new users.
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 Главное меню"),
            BotCommand(command="meme", description="😂 Новый фотомем"),
            BotCommand(command="helpme", description="🆘 Срочная помощь"),
        ])
    except Exception as exc:
        print(f"Telegram command menu update failed: {type(exc).__name__}", flush=True)
    task = asyncio.create_task(reminder_loop(bot))
    followups_task = asyncio.create_task(v2_experience.followup_loop(bot, connect, kb))
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        followups_task.cancel()
        await asyncio.gather(task, followups_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
