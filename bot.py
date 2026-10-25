"""НЕ НАТВОРИ ДЕЛОВ — private harm-reduction companion."""
import asyncio
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, BufferedInputFile, InputMediaPhoto
from dotenv import load_dotenv
from context_memes import image as context_image

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DB = Path(__file__).with_name("tracker.db")
SUBSTANCES = ["Мефедрон", "Альфа-ПВП", "Амфетамин", "Метамфетамин", "Каннабис", "MDMA", "Кокаин", "Опиоиды", "Бензодиазепины", "ЛСД", "Псилоцибин", "Другое"]
dp = Dispatcher()
CHECKS = [(30, "🗿 Министерство здравого смысла: как дыхание, сознание и самочувствие? Не смешивай вещества и не садись за руль."),
          (90, "📞 Кремль на связи: ты как? Есть сильная тревога, перегрев, боль в груди или проблемы с дыханием? При опасных симптомах вызывай экстренную помощь."),
          (240, "🫀 Проверка связи: есть возможность отдохнуть, поесть и связаться с человеком, которому доверяешь?"),
          (720, "🌙 Как состояние? Если не спал, отложи важные решения. При ухудшении самочувствия обратись за помощью.")]

def db():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    return c

def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, substance TEXT);
        CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, substance TEXT NOT NULL, started TEXT NOT NULL, ended TEXT);
        CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, at TEXT NOT NULL, note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS goals (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, title TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS companion (user_id INTEGER PRIMARY KEY, reminders INTEGER NOT NULL DEFAULT 0, quiet INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS checks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, session_id INTEGER, due TEXT NOT NULL, body TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS moods (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, at TEXT NOT NULL, mood TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS focus_goals (user_id INTEGER PRIMARY KEY, no_ex INTEGER NOT NULL DEFAULT 0);
        """)

def now():
    return datetime.now(timezone.utc)

def iso(dt=None):
    return (dt or now()).isoformat(timespec="seconds")

def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=v) for t, v in row] for row in rows])

def menu():
    return kb([("📝 Записать", "record"), ("🫀 Как я?", "feel")],
              [("🚫 Не звонить бывшей", "ex_toggle"), ("⚙️ Ещё", "more")])

def active(c, uid):
    return c.execute("SELECT * FROM sessions WHERE user_id=? AND ended IS NULL ORDER BY id DESC LIMIT 1", (uid,)).fetchone()

async def show(message, text, markup=None):
    try:
        if message.photo:
            await message.delete()
            await message.answer(text, reply_markup=markup)
        else:
            await message.edit_text(text, reply_markup=markup)
    except Exception:
        await message.answer(text, reply_markup=markup)

async def home(message, uid):
    with db() as c:
        last = c.execute("SELECT substance,started FROM sessions WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        mood = c.execute("SELECT mood FROM moods WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        current = active(c, uid)
    text = "🗿 НЕ НАТВОРИ ДЕЛОВ\n\nТвой журнал. Без осуждения."
    text += "\n\n🫀 Самочувствие: " + (mood["mood"] if mood else "не отмечено")
    text += "\n📝 Последняя запись: " + (last["substance"] + " · " + last["started"][:10] if last else "пока нет")
    if current:
        text += "\n🔴 Запись открыта"
    text += "\n\nЕсли плохо — нажми «Как я?» и выбери помощь."
    await show(message, text, menu())

@dp.message(CommandStart())
async def start(m: Message):
    await home(m, m.from_user.id)

@dp.callback_query(F.data == "home")
async def back(q: CallbackQuery):
    await q.answer()
    await home(q.message, q.from_user.id)

@dp.callback_query(F.data == "record")
async def record(q: CallbackQuery):
    await q.answer()
    with db() as c:
        current = active(c, q.from_user.id)
    if current:
        await show(q.message, "🔴 Запись употребления открыта.\n\nМожно проверить состояние или завершить запись. Таймер не показывает, когда употребление становится безопасным.",
                   kb([("🫀 Как я?", "feel"), ("⏹ Закончить запись", "end")], [("🏠 Меню", "home")]))
    else:
        rows = [[(SUBSTANCES[i], "sub:" + str(i)), (SUBSTANCES[i+1], "sub:" + str(i+1))] for i in range(0, len(SUBSTANCES), 2)]
        await show(q.message, "📝 Что записываем?\nВыбери вещество. Дозировку указывать не нужно.", kb(*rows, [("🏠 Меню", "home")]))

@dp.callback_query(F.data.startswith("sub:"))
async def choose(q: CallbackQuery):
    try:
        idx = int(q.data.split(":")[1])
        substance = SUBSTANCES[idx]
    except (ValueError, IndexError):
        return await q.answer("Неизвестный выбор", show_alert=True)
    with db() as c:
        current = active(c, q.from_user.id)
        if current:
            return await q.answer("Сначала заверши текущую запись", show_alert=True)
        c.execute("INSERT INTO sessions(user_id,substance,started) VALUES(?,?,?)", (q.from_user.id, substance, iso()))
        sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO users(user_id,substance) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET substance=excluded.substance", (q.from_user.id, substance))
        settings = c.execute("SELECT reminders FROM companion WHERE user_id=?", (q.from_user.id,)).fetchone()
        if settings and settings["reminders"]:
            schedule(c, q.from_user.id, sid)
    await q.answer("Записано")
    await send_context_meme(q.message, "record")
    await show(q.message, f"📝 {substance} — записано.\n\nНе смешивай вещества, не садись за руль. Если самочувствие ухудшается, обратись за помощью.\n\nВключить ненавязчивые проверки состояния?",
               kb([("🔔 Включить", "notify_on"), ("🔕 Не сейчас", "home")], [("🫀 Как я?", "feel")]))

def schedule(c, uid, sid):
    c.execute("DELETE FROM checks WHERE user_id=? AND sent=0", (uid,))
    c.execute("INSERT INTO checks(user_id,session_id,due,body) VALUES(?,?,?,?)",
              (uid, sid, iso(now()+timedelta(minutes=30)), "🗿 Проверка связи: как самочувствие?"))

@dp.callback_query(F.data == "ex_toggle")
async def ex_toggle(q: CallbackQuery):
    uid = q.from_user.id
    with db() as c:
        row = c.execute("SELECT no_ex FROM focus_goals WHERE user_id=?", (uid,)).fetchone()
        enabled = not bool(row and row["no_ex"])
        c.execute("INSERT INTO focus_goals(user_id,no_ex) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET no_ex=excluded.no_ex", (uid, int(enabled)))
    await q.answer("Цель включена" if enabled else "Цель выключена")
    await show(q.message,
        "🚫 ЦЕЛЬ: НЕ ЗВОНИТЬ БЫВШЕЙ\\n\\n" +
        ("Включена. Во время открытой записи и при включённых уведомлениях буду напоминать каждые 30 минут. Это обычные уведомления Telegram со звуком, не телефонный звонок." if enabled else "Выключена.") +
        "\\n\\nЧтобы напоминания приходили, включи уведомления в настройках.",
        kb([("🔔 Уведомления", "notifications")], [("🏠 Меню", "home")]))


@dp.callback_query(F.data == "end")
async def end(q: CallbackQuery):
    with db() as c:
        current = active(c, q.from_user.id)
        if current:
            c.execute("UPDATE sessions SET ended=? WHERE id=?", (iso(), current["id"]))
            c.execute("DELETE FROM checks WHERE user_id=? AND sent=0", (q.from_user.id,))
    await q.answer("Запись закрыта")
    await home(q.message, q.from_user.id)

@dp.callback_query(F.data == "feel")
async def feel(q: CallbackQuery):
    await q.answer()
    await show(q.message, "🫀 КАК ТЫ?\n\nЕсли есть сильная боль в груди, судороги, потеря сознания, затруднённое дыхание или сильный перегрев — сразу вызывай экстренную помощь.",
               kb([("😎 Нормально", "mood:ok"), ("😵 Тяжело", "mood:bad")],
                  [("🆘 Нужна помощь", "help")], [("🏠 Меню", "home")]))

@dp.callback_query(F.data.startswith("mood:"))
async def mood(q: CallbackQuery):
    value = "нормально" if q.data == "mood:ok" else "тяжело"
    with db() as c:
        c.execute("INSERT INTO moods(user_id,at,mood) VALUES(?,?,?)", (q.from_user.id, iso(), value))
    await q.answer("Сохранено")
    await send_context_meme(q.message, "ok" if value == "нормально" else "bad")
    if value == "тяжело":
        await show(q.message, "🫀 Отметил: тяжело.\n\nПобудь рядом с человеком, которому доверяешь. Если появились опасные симптомы — вызывай экстренную помощь. Не принимай новые вещества, пытаясь исправить состояние.",
                   kb([("🆘 Помощь", "help")], [("🏠 Меню", "home")]))
    else:
        await home(q.message, q.from_user.id)

@dp.callback_query(F.data == "help")
async def help_page(q: CallbackQuery):
    await q.answer()
    await show(q.message, "🆘 ЕСЛИ ПЛОХО\n\nПри судорогах, потере сознания, нарушении дыхания, сильной боли в груди, перегреве или выраженной спутанности — звони в местную экстренную службу (112 в России).\n\nНе оставайся один. Если человек без сознания, но дышит нормально — положи его на бок. Если не дышит нормально — следуй указаниям диспетчера по реанимации.\n\nСообщи медикам, что и когда было принято. Не смешивай вещества и не садись за руль.",
               kb([("🏠 Меню", "home")]))

async def send_context_meme(message, event):
    try:
        picture = await context_image(event)
        await message.answer_photo(BufferedInputFile(picture, filename="meme.png"),
                                   caption="🗿 Пародийный мем по разделу, не реальная цитата.")
    except Exception:
        pass

@dp.callback_query(F.data == "more")
async def more(q: CallbackQuery):
    await q.answer()
    await show(q.message, "⚙️ ЕЩЁ\n\nИстория, уведомления, безопасность и данные.", kb(
        [("📊 История", "history"), ("🔔 Уведомления", "notifications")],
        [("🆘 Помощь", "help"), ("🗑 Данные", "privacy")],
        [("🏠 Меню", "home")]))

@dp.callback_query(F.data == "history")
async def history(q: CallbackQuery):
    with db() as c:
        rows = c.execute("SELECT substance,started FROM sessions WHERE user_id=? ORDER BY id DESC LIMIT 1 OFFSET 0", (q.from_user.id,)).fetchall()
        count = c.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (q.from_user.id,)).fetchone()[0]
        recent = c.execute("SELECT substance,started FROM sessions WHERE user_id=? ORDER BY id DESC LIMIT 7", (q.from_user.id,)).fetchall()
    text = f"📊 ИСТОРИЯ\nВсего записей: {count}\n\n"
    text += "\n".join(f"• {x['started'][:16].replace('T',' ')} UTC — {x['substance']}" for x in recent) or "Записей пока нет."
    text += "\n\nЭто только журнал, не оценка здоровья или трезвости."
    await q.answer()
    await send_context_meme(q.message, "history")
    await show(q.message, text, kb([("🏠 Меню", "home")]))

@dp.callback_query(F.data == "notifications")
async def notifications(q: CallbackQuery):
    with db() as c:
        row = c.execute("SELECT reminders,quiet FROM companion WHERE user_id=?", (q.from_user.id,)).fetchone()
    enabled = bool(row and row["reminders"])
    await q.answer()
    await show(q.message, f"🔔 ПРОВЕРКИ СОСТОЯНИЯ\n\nСейчас: {'включены' if enabled else 'выключены'}\n\nПосле новой записи бот может присылать несколько проверок самочувствия. Это не таймер безопасного повторного употребления.\n\nОтключить можно в любой момент.",
               kb([("🔔 Включить", "notify_on"), ("🔕 Выключить", "notify_off")], [("🏠 Меню", "home")]))

@dp.callback_query(F.data.in_({"notify_on", "notify_off"}))
async def notify_toggle(q: CallbackQuery):
    enabled = int(q.data == "notify_on")
    with db() as c:
        c.execute("INSERT INTO companion(user_id,reminders) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET reminders=excluded.reminders", (q.from_user.id, enabled))
        if not enabled:
            c.execute("DELETE FROM checks WHERE user_id=? AND sent=0", (q.from_user.id,))
        else:
            current = active(c, q.from_user.id)
            if current:
                schedule(c, q.from_user.id, current["id"])
    await q.answer("Уведомления включены" if enabled else "Уведомления выключены")
    await home(q.message, q.from_user.id)

@dp.callback_query(F.data == "privacy")
async def privacy(q: CallbackQuery):
    await q.answer()
    await show(q.message, "🗑 ДАННЫЕ\n\nУдаление очищает твои данные из рабочей SQLite-базы. Резервные копии VPS нужно очищать отдельно.",
               kb([("🗑 Удалить мои данные", "delete_ask")], [("🏠 Меню", "home")]))

@dp.callback_query(F.data == "delete_ask")
async def delete_ask(q: CallbackQuery):
    await q.answer()
    await show(q.message, "⚠️ Удалить все записи, самочувствие, уведомления и настройки? Это нельзя отменить.",
               kb([("Да, удалить", "delete_yes"), ("Отмена", "privacy")]))

@dp.callback_query(F.data == "delete_yes")
async def delete_yes(q: CallbackQuery):
    uid = q.from_user.id
    with db() as c:
        c.execute("DELETE FROM entries WHERE session_id IN (SELECT id FROM sessions WHERE user_id=?)", (uid,))
        c.execute("DELETE FROM goals WHERE session_id IN (SELECT id FROM sessions WHERE user_id=?)", (uid,))
        for table in ("sessions", "users", "companion", "checks", "moods", "focus_goals"):
            c.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
    await q.answer("Удалено")
    await home(q.message, uid)

async def reminder_loop(bot):
    while True:
        try:
            with db() as c:
                pending = c.execute("SELECT id,user_id,session_id,body FROM checks WHERE sent=0 AND due<=? ORDER BY id LIMIT 30", (iso(),)).fetchall()
            for item in pending:
                with db() as c:
                    enabled = c.execute("SELECT reminders FROM companion WHERE user_id=?", (item["user_id"],)).fetchone()
                    session = active(c, item["user_id"])
                    focus = c.execute("SELECT no_ex FROM focus_goals WHERE user_id=?", (item["user_id"],)).fetchone()
                    valid = bool(enabled and enabled["reminders"] and session and session["id"] == item["session_id"])
                    # 24-hour hard cap: prevent an accidentally forgotten session from notifying forever.
                    if valid and (now() - datetime.fromisoformat(session["started"])).total_seconds() > 86400:
                        valid = False
                    c.execute("UPDATE checks SET sent=1 WHERE id=?", (item["id"],))
                    if valid:
                        c.execute("INSERT INTO checks(user_id,session_id,due,body) VALUES(?,?,?,?)",
                                  (item["user_id"], session["id"], iso(now()+timedelta(minutes=30)), item["body"]))
                if not valid:
                    continue
                is_ex = bool(focus and focus["no_ex"])
                msg = ("🚫 НЕ ЗВОНИ БЫВШЕЙ. Отложи сообщения и решения до завтра.\\n\\n" if is_ex else "")
                msg += "🫀 Проверка связи: как дыхание, сознание и самочувствие? Если опасные симптомы — вызывай экстренную помощь."
                try:
                    picture = await context_image("ex" if is_ex else "reminder")
                    await bot.send_photo(item["user_id"], BufferedInputFile(picture, filename="reminder.png"),
                                         caption=msg, disable_notification=False,
                                         reply_markup=kb([("🫀 Как я?", "feel"), ("⏹ Закончить", "end")],
                                                         [("🔕 Выключить", "notify_off")]))
                except Exception:
                    try:
                        await bot.send_message(item["user_id"], msg, disable_notification=False)
                    except Exception:
                        pass
        except Exception:
            import logging
            logging.exception("Reminder worker error")
        await asyncio.sleep(30)

async def main():
    if not TOKEN or TOKEN == "put_your_token_here":
        raise RuntimeError("Укажи BOT_TOKEN в .env")
    init()
    bot = Bot(TOKEN)
    worker = asyncio.create_task(reminder_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
