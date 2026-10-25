"""Pirate geocaching quest: private personal game, temporary clue display."""
import asyncio
import hashlib
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
    c.commit()
    return c

def digest(word, salt):
    return hashlib.pbkdf2_hmac("sha256", word.encode("utf-8"), salt, 160000)

def authorized(uid, word):
    with db() as c:
        p=c.execute("SELECT * FROM players WHERE user_id=?", (uid,)).fetchone()
    return bool(p and hmac.compare_digest(p["secret"], digest(word, p["salt"])))

async def harbor(m, uid):
    with db() as c:
        rows=c.execute("SELECT id,title FROM treasures WHERE user_id=? ORDER BY id DESC LIMIT 30",(uid,)).fetchall()
    opts=[[(f"🗺️ {r['title'][:40]}",f"pq:view:{r['id']}")] for r in rows]
    opts += [[("➕ Создать сокровище","pq:new")],[("🚪 Закрыть гавань","pq:exit")]]
    await m.answer("🏴‍☠️ ТАЙНАЯ ГАВАНЬ\\n\\nВыбери своё сокровище:",reply_markup=buttons(opts))

def install(dp):
    @dp.message(F.text.regexp(r"(?i)^клады$"))
    async def enter(m: Message,state:FSMContext):
        if m.chat.type!="private": return
        await state.clear()
        await state.set_state(QuestForm.password)
        await m.answer("🏴‍☠️ Тайная гавань\\n\\nВведи своё кодовое слово. При первом входе оно будет создано.")

    @dp.message(QuestForm.password)
    async def password(m:Message,state:FSMContext):
        word=(m.text or "").strip()
        if not 6<=len(word)<=100:
            return await m.answer("Кодовое слово должно содержать от 6 до 100 символов.")
        uid=m.from_user.id
        with db() as c:
            exists=c.execute("SELECT 1 FROM players WHERE user_id=?",(uid,)).fetchone()
            if not exists:
                salt=os.urandom(16)
                c.execute("INSERT INTO players VALUES(?,?,?)",(uid,salt,digest(word,salt)))
        try: await m.delete()
        except Exception: pass
        if not authorized(uid,word):
            return await m.answer("🔒 Неверное кодовое слово.")
        await state.update_data(pirate_ok=True)
        await state.set_state(None)
        await harbor(m,uid)

    async def guard(q,state):
        if not (await state.get_data()).get("pirate_ok"):
            await q.answer("Введи «клады» и кодовое слово.",show_alert=True)
            return False
        return True

    @dp.callback_query(F.data=="pq:exit")
    async def exit_harbor(q:CallbackQuery,state:FSMContext):
        await state.clear()
        await q.answer("Гавань закрыта")
        await q.message.edit_text("🏴‍☠️ Гавань закрыта.")

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
        await q.message.answer("🏴‍☠️ Как назовём сокровище?")

    @dp.message(QuestForm.title)
    async def title(m:Message,state:FSMContext):
        title=(m.text or "").strip()
        if not 1<=len(title)<=60:return await m.answer("Название — от 1 до 60 символов.")
        await state.update_data(title=title)
        await state.set_state(QuestForm.photo)
        await m.answer("📸 Отправь фотографию-подсказку.")

    @dp.message(QuestForm.photo)
    async def photo(m:Message,state:FSMContext):
        if not m.photo:return await m.answer("Нужна фотография.")
        await state.update_data(photo_id=m.photo[-1].file_id)
        await state.set_state(QuestForm.location)
        await m.answer("📍 Отправь геолокацию Telegram или координаты в формате 55.75, 37.62.")

    @dp.message(QuestForm.location)
    async def location(m:Message,state:FSMContext):
        if m.location:
            lat,lon=m.location.latitude,m.location.longitude
        else:
            match=re.fullmatch(r"\\s*(-?\\d+(?:\\.\\d+)?)\\s*[,; ]\\s*(-?\\d+(?:\\.\\d+)?)\\s*",m.text or "")
            if not match:return await m.answer("Отправь геолокацию или координаты: 55.75, 37.62")
            lat,lon=map(float,match.groups())
        if not -90<=lat<=90 or not -180<=lon<=180:
            return await m.answer("Некорректные координаты.")
        data=await state.get_data()
        with db() as c:
            c.execute("INSERT INTO treasures(user_id,title,photo_id,latitude,longitude) VALUES(?,?,?,?,?)",
                      (m.from_user.id,data["title"],data["photo_id"],lat,lon))
        await state.set_state(None)
        await m.answer("✅ Сокровище сохранено!")
        await harbor(m,m.from_user.id)

    @dp.callback_query(F.data.startswith("pq:view:"))
    async def view(q:CallbackQuery,state:FSMContext):
        if not await guard(q,state):return
        try: tid=int(q.data.split(":")[-1])
        except ValueError:return
        with db() as c:
            row=c.execute("SELECT * FROM treasures WHERE id=? AND user_id=?",(tid,q.from_user.id)).fetchone()
        if not row:return await q.answer("Сокровище не найдено.",show_alert=True)
        await q.answer()
        await q.message.answer_location(row["latitude"],row["longitude"])
        await q.message.answer_photo(row["photo_id"],caption=f"🏴‍☠️ {row['title']}",
            reply_markup=buttons([[("🏴‍☠️ Я пришёл на место",f"pq:arrive:{tid}")]]))

    @dp.callback_query(F.data.startswith("pq:arrive:"))
    async def arrive(q:CallbackQuery,state:FSMContext):
        if not await guard(q,state):return
        await q.answer("🚨 Королевский патруль приближается!")
        try:
            await q.message.edit_caption(caption="🚨 Патруль приближается! Подсказка исчезнет через минуту.")
        except Exception:pass
        async def expire():
            await asyncio.sleep(60)
            try:
                await q.message.delete()
            except Exception:pass
        asyncio.create_task(expire())
