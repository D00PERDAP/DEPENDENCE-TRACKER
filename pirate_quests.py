"""Pirate geocaching quest: private personal game, temporary clue display."""
import asyncio
import hashlib
import logging
import hmac
import os
import re
import sqlite3
from pathlib import Path
from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "pirate_quests.sqlite3"
# Temporary game messages are tracked per user for chat cleanup.
GAME_MESSAGES = {}
GAME_TIMERS = {}
GAME_SCREEN = {}
logger = logging.getLogger(__name__)

class QuestForm(StatesGroup):
    password = State()
    title = State()
    photo = State()
    location = State()

def buttons(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t,d in row] for row in rows])

def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS players
        (user_id INTEGER PRIMARY KEY, salt BLOB NOT NULL, secret BLOB NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS treasures
        (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
         title TEXT NOT NULL, photo_id TEXT NOT NULL, latitude REAL NOT NULL,
         longitude REAL NOT NULL, FOREIGN KEY(user_id) REFERENCES players(user_id))""")
    c.execute("CREATE TABLE IF NOT EXISTS banned_players (user_id INTEGER PRIMARY KEY)")
    c.commit()
    return c

def is_banned(uid):
    with db() as c:
        return c.execute("SELECT 1 FROM banned_players WHERE user_id=?", (uid,)).fetchone() is not None

def digest(word, salt):
    return hashlib.pbkdf2_hmac("sha256", word.encode("utf-8"), salt, 160000)

def authorized(uid, word):
    with db() as c:
        p=c.execute("SELECT * FROM players WHERE user_id=?", (uid,)).fetchone()
    return bool(p and hmac.compare_digest(p["secret"], digest(word, p["salt"])))

async def track(uid, message):
    item = (message.chat.id, message.message_id)
    messages = GAME_MESSAGES.setdefault(uid, [])
    if item not in messages:
        messages.append(item)
    return message

async def delete_input(message):
    try:
        await message.delete()
    except Exception as exc:
        logger.warning("Cannot delete input %s: %s", message.message_id, exc)

async def clear_except_screen(bot, uid):
    screen = GAME_SCREEN.get(uid)
    kept = []
    for item in GAME_MESSAGES.get(uid, []):
        if item == screen:
            kept.append(item)
            continue
        try:
            await bot.delete_message(*item)
        except Exception as exc:
            logger.warning("Cannot delete game message %s: %s", item, exc)
    GAME_MESSAGES[uid] = kept

async def show_prompt(message, uid, text):
    await clear_except_screen(message.bot, uid)
    screen = GAME_SCREEN.get(uid)
    if screen:
        try:
            await message.bot.edit_message_text(text, chat_id=screen[0], message_id=screen[1])
            return
        except Exception as exc:
            logger.warning("Cannot edit game screen %s: %s", screen, exc)
    sent = await track(uid, await message.answer(text))
    GAME_SCREEN[uid] = (sent.chat.id, sent.message_id)

async def clear_game(bot, uid):
    timer = GAME_TIMERS.pop(uid, None)
    if timer and timer is not asyncio.current_task():
        timer.cancel()
    messages = GAME_MESSAGES.pop(uid, [])
    screen = GAME_SCREEN.pop(uid, None)
    if screen and screen not in messages:
        messages.append(screen)
    for chat_id, message_id in reversed(messages):
        try:
            await bot.delete_message(chat_id, message_id)
        except Exception as exc:
            logger.warning("Cannot delete game message %s: %s", message_id, exc)

async def harbor(m, uid):
    with db() as c:
        rows=c.execute("SELECT id,title FROM treasures WHERE user_id=? ORDER BY id DESC LIMIT 30",(uid,)).fetchall()
    from admin_center import db as admin_db
    with admin_db() as ac:
        hidden = {r[0] for r in ac.execute("SELECT treasure_id FROM hidden_treasures")}
    rows = [r for r in rows if r["id"] not in hidden]
    opts=[[(f"🗺️ {r['title'][:40]}",f"pq:view:{r['id']}")] for r in rows]
    opts += [[("➕ Поставить свою точку","pq:new")],[("🚪 Закрыть гавань, брат","pq:exit")]]
    content = "🏴‍☠️ ТАЙНАЯ ГАВАНЬ, ВАЦОК\n\nТихо, брат, тут только твои личные тайники. Выбирай:"
    await clear_except_screen(m.bot, uid)
    screen = GAME_SCREEN.get(uid)
    if screen:
        try:
            await m.bot.edit_message_text(content, chat_id=screen[0], message_id=screen[1], reply_markup=buttons(opts))
            return
        except Exception:
            pass
    sent = await track(uid, await m.answer(content, reply_markup=buttons(opts)))
    GAME_SCREEN[uid] = (sent.chat.id, sent.message_id)

def install(dp):
    @dp.message(F.text.regexp(r"(?i)^клады$"))
    async def enter(m: Message,state:FSMContext):
        if m.chat.type!="private": return
        if is_banned(m.from_user.id):
            try: await m.delete()
            except Exception: pass
            return await m.answer("🚫 В гавань вход закрыт, вацок.")
        await state.clear()
        await clear_game(m.bot, m.from_user.id)
        await delete_input(m)
        await state.set_state(QuestForm.password)
        await show_prompt(m, m.from_user.id, "🏴‍☠️ Тайная гавань, брат\n\nСкажи кодовое слово, брат. Первый раз — придумаешь своё.")

    @dp.message(QuestForm.password)
    async def password(m:Message,state:FSMContext):
        word=(m.text or "").strip()
        await delete_input(m)
        if not 6<=len(word)<=100:
            return await show_prompt(m, m.from_user.id, "Э, вацок, кодовое слово должно быть от 6 до 100 символов.")
        uid=m.from_user.id
        with db() as c:
            exists=c.execute("SELECT 1 FROM players WHERE user_id=?",(uid,)).fetchone()
            if not exists:
                salt=os.urandom(16)
                c.execute("INSERT INTO players VALUES(?,?,?)",(uid,salt,digest(word,salt)))
        if not authorized(uid,word):
            return await show_prompt(m, uid, "🔒 Неверное кодовое слово.")
        await state.update_data(pirate_ok=True)
        await state.set_state(None)
        await harbor(m,uid)

    async def guard(q,state):
        if is_banned(q.from_user.id):
            await state.clear()
            await q.answer("Доступ к гавани закрыт.", show_alert=True)
            return False
        if not (await state.get_data()).get("pirate_ok"):
            await q.answer("Введи «клады» и кодовое слово.",show_alert=True)
            return False
        return True

    @dp.callback_query(F.data=="pq:exit")
    async def exit_harbor(q:CallbackQuery,state:FSMContext):
        await state.clear()
        await q.answer("Гавань закрыта")
        await clear_game(q.bot, q.from_user.id)

    @dp.callback_query(F.data=="pq:home")
    async def home(q:CallbackQuery,state:FSMContext):
        if not await guard(q,state): return
        await q.answer()
        await harbor(q.message,q.from_user.id)

    @dp.callback_query(F.data=="pq:new")
    async def new(q:CallbackQuery,state:FSMContext):
        if not await guard(q,state):return
        await q.answer()
        await state.set_state(QuestForm.title)
        await show_prompt(q.message, q.from_user.id, "🏴‍☠️ Как назовём сокровище?")

    @dp.message(QuestForm.title)
    async def title(m:Message,state:FSMContext):
        title=(m.text or "").strip()
        await delete_input(m)
        if not 1<=len(title)<=60:return await show_prompt(m, m.from_user.id, "Название — от 1 до 60 символов.")
        await state.update_data(title=title)
        await state.set_state(QuestForm.photo)
        await show_prompt(m, m.from_user.id, "📸 Отправь фотографию-подсказку.")

    @dp.message(QuestForm.photo)
    async def photo(m:Message,state:FSMContext):
        photo_id = m.photo[-1].file_id if m.photo else None
        await delete_input(m)
        if not photo_id:return await show_prompt(m, m.from_user.id, "Нужна фотография.")
        await state.update_data(photo_id=photo_id)
        await state.set_state(QuestForm.location)
        await show_prompt(m, m.from_user.id, "📍 Отправь геолокацию Telegram или координаты в формате 55.75, 37.62.")

    @dp.message(QuestForm.location)
    async def location(m:Message,state:FSMContext):
        location = m.location
        raw_text = m.text or ""
        await delete_input(m)
        if location:
            lat,lon=location.latitude,location.longitude
        else:
            match=re.fullmatch(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;\s]+\s*(-?\d{1,3}(?:\.\d+)?)\s*", raw_text)
            if not match:
                return await show_prompt(m, m.from_user.id, "Отправь координаты, например: 55.751244123, 37.618423456")
            lat,lon=(float(v.replace(",", ".")) for v in match.groups())
        if not -90<=lat<=90 or not -180<=lon<=180:
            return await show_prompt(m, m.from_user.id, "Некорректные координаты.")
        data=await state.get_data()
        with db() as c:
            c.execute("INSERT INTO treasures(user_id,title,photo_id,latitude,longitude) VALUES(?,?,?,?,?)",
                      (m.from_user.id,data["title"],data["photo_id"],lat,lon))
        await state.set_state(None)
        await harbor(m,m.from_user.id)

    @dp.callback_query(F.data.startswith("pq:view:"))
    async def view(q:CallbackQuery,state:FSMContext):
        if not await guard(q,state):return
        try: tid=int(q.data.split(":")[-1])
        except ValueError:return
        with db() as c:
            row=c.execute("SELECT * FROM treasures WHERE id=? AND user_id=?",(tid,q.from_user.id)).fetchone()
        if not row:return await q.answer("Э, брат, такой точки нет.",show_alert=True)
        from admin_center import db as admin_db
        with admin_db() as ac:
            if ac.execute("SELECT 1 FROM hidden_treasures WHERE treasure_id=?", (tid,)).fetchone():
                return await q.answer("Сокровище временно скрыто.", show_alert=True)
        await q.answer()
        await clear_except_screen(q.bot, q.from_user.id)
        await track(q.from_user.id, await q.message.answer_location(row["latitude"],row["longitude"]))
        await track(q.from_user.id, await q.message.answer_photo(row["photo_id"],caption=f"🏴‍☠️ {row['title']}",
            reply_markup=buttons([[("🏴‍☠️ Я пришёл на место",f"pq:arrive:{tid}")]])))

    @dp.callback_query(F.data.startswith("pq:arrive:"))
    async def arrive(q:CallbackQuery,state:FSMContext):
        if not await guard(q,state):return
        await q.answer("🚨 Королевский патруль приближается!")
        try:
            await q.message.edit_caption(caption="🚨 Патруль приближается! Подсказка исчезнет через минуту.")
        except Exception:pass
        uid=q.from_user.id
        await state.clear()
        async def expire():
            try:
                await asyncio.sleep(60)
                await clear_game(q.bot, uid)
            finally:
                GAME_TIMERS.pop(uid, None)
        old = GAME_TIMERS.pop(uid, None)
        if old: old.cancel()
        GAME_TIMERS[uid] = asyncio.create_task(expire())
