import asyncio
import io
import os
import random
import sqlite3
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, BufferedInputFile
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DB = Path(__file__).with_name("tracker.db")
PRESETS = [
    "Не звонить бывшей", "Не названивать друзьям",
    "Не ехать за приключениями", "Не тратить лишние деньги",
    "Не садиться за руль", "Не писать лишнего",
    "Не смешивать вещества", "Не оставаться одному, если плохо",
]
SUBSTANCES = ["Мефедрон", "Альфа-ПВП", "Амфетамин", "Метамфетамин",
              "Каннабис", "MDMA", "Кокаин", "Опиоиды",
              "Бензодиазепины", "ЛСД", "Псилоцибин", "Другое", "Не указывать"]
INTERVALS = (15, 30, 60)
dp = Dispatcher()


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
        """)


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


def active(c, uid):
    return c.execute("SELECT * FROM sessions WHERE user_id=? AND ended IS NULL ORDER BY id DESC LIMIT 1", (uid,)).fetchone()


def ensure_preset(c, uid):
    n = c.execute("SELECT COUNT(*) FROM checklist WHERE user_id=?", (uid,)).fetchone()[0]
    if n == 0:
        c.executemany("INSERT INTO checklist(user_id,title,selected) VALUES(?,?,0)",
                      [(uid, text) for text in PRESETS])


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
    if running:
        return kb([[("☑️ Мои запреты", "check")],
                   [("🫀 Как я?", "health"), ("⏹ Закончить", "finish")],
                   [("📊 Статистика", "stats"), ("⚙️ Ещё", "more")]])
    return kb([[("☑️ Я НЕ ХОЧУ", "check")],
               [("📊 Статистика", "stats"), ("⚙️ Ещё", "more")]])


async def home(message, uid):
    with connect() as c:
        session = active(c, uid)
        ensure_preset(c, uid)
        count = len(picked(c, uid))
    if session:
        text = f"💀 НЕ НАТВОРИ ДЕЛОВ\n\n🔴 Сопровождение включено.\nТвои запреты: {count}\n\nЕсли тебе плохо — нажми «Как я?»."
    else:
        text = "💀 НЕ НАТВОРИ ДЕЛОВ\n\nВыбери, чего НЕ хочешь натворить.\nПотом включи напоминания. Всё."
    await display(message, text, home_buttons(bool(session)))


@dp.message(Command("meme"))
async def send_test_meme(m: Message):
    """Send one approved local meme on request, never an unreviewed web image."""
    path = choose_meme("general")
    if not path:
        for category in ("ex", "calls", "money", "travel"):
            path = choose_meme(category)
            if path:
                break
    if not path:
        await m.answer("Пока нет проверенных мемов. Сначала добавь картинку через meme_collector.py approve.")
        return
    from aiogram.types import FSInputFile
    await m.answer_photo(FSInputFile(path), caption="💀 Не натвори делов. Ты сам выбираешь, чего не делать.")


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
    markup += [[("▶️ Начать" if not session else "🔴 В процессе", "substance" if not session else "home")]]
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
    with connect() as c:
        rows, weeks, substances, durations = month_data(c, q.from_user.id)
    month = utcnow().strftime("%m.%Y")
    bars = "\n".join(f"{i+1} нед.  {'█'*n or '·'}  {n}" for i, n in enumerate(weeks))
    names = "\n".join(f"• {name}: {num}" for name, num in substances.most_common())
    duration = f"Средняя длительность записи: {sum(durations)/len(durations):.1f} ч\n" if durations else ""
    txt = (f"📊 СТАТИСТИКА · {month} (UTC)\n\n"
           f"Записей о сессиях: {len(rows)}\n"
           f"{duration}\n"
           f"{bars}\n\n"
           f"Что указано:\n{names or 'Пока пусто.'}\n\n"
           "Статистика основана только на собственных отметках, а не на реальном измерении употребления.")
    await q.answer()
    await display(q.message, txt, kb([[("🏠 Меню", "home")], [("🗑 Мои данные", "delete_ask")]]))


@dp.callback_query(F.data == "more")
async def more(q: CallbackQuery):
    await q.answer()
    await display(q.message, "⚙️ ЕЩЁ\n\nТвои записи видны только в этом чате. Удаление из базы не удаляет резервные копии сервера.", kb([
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
    await q.answer("Удалено")
    await display(q.message, "История удалена из активной базы. Резервные копии на сервере удаляются отдельно.", kb([[("🏠 Меню", "home")]]))


def choose_meme(category, previous=None):
    import random
    directory = Path(__file__).with_name("assets") / "memes" / category
    if not directory.exists():
        return None
    items = [x for x in directory.iterdir() if x.is_file() and x.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and x.stat().st_size > 0]
    if not items:
        return None
    filtered = [x for x in items if x.name != previous] or items
    return random.choice(filtered)


async def reminder_loop(bot):
    while True:
        try:
            current = utcnow()
            with connect() as c:
                due = c.execute("SELECT * FROM alerts WHERE next_at<=? ORDER BY next_at LIMIT 50", (current.isoformat(),)).fetchall()
            for alert in due:
                uid, sid = alert["user_id"], alert["session_id"]
                with connect() as c:
                    ses = c.execute("SELECT * FROM sessions WHERE id=? AND user_id=? AND ended IS NULL", (sid, uid)).fetchone()
                    if not ses:
                        c.execute("DELETE FROM alerts WHERE user_id=? AND session_id=?", (uid,sid))
                        continue
                    began = datetime.fromisoformat(ses["started"])
                    if current - began >= timedelta(hours=24):
                        c.execute("DELETE FROM alerts WHERE user_id=? AND session_id=?", (uid,sid))
                        c.execute("UPDATE sessions SET ended=? WHERE id=? AND ended IS NULL", (iso(),sid))
                        continue
                    selected = picked(c, uid)
                    next_time = (current + timedelta(minutes=alert["interval_min"])).isoformat(timespec="seconds")
                    c.execute("UPDATE alerts SET next_at=? WHERE user_id=? AND session_id=?",
                              (next_time,uid,sid))
                titles = [x["title"] for x in selected]
                if not titles:
                    continue
                text = "🔔 ТЫ САМ РЕШИЛ:\n\n" + "\n".join("☑️ " + t for t in titles[:8]) + "\n\nНе принимай импульсивных решений. Если плохо — обращайся за помощью."
                try:
                    # Opt-in only. Curated, local memes; no external image lookups, no AI.
                    category = "general"
                    lower = " ".join(titles).lower()
                    if "бывш" in lower: category = "ex"
                    elif "друз" in lower or "звон" in lower: category = "calls"
                    elif "деньг" in lower or "трат" in lower: category = "money"
                    elif "ехат" in lower or "руль" in lower: category = "travel"
                    path = choose_meme(category) or choose_meme("general")
                    if path:
                        from aiogram.types import FSInputFile
                        await bot.send_photo(uid, FSInputFile(path), caption=text[:1024])
                    else:
                        await bot.send_message(uid, text)
                except Exception as exc:
                    print(f"Reminder delivery error for user {uid}: {type(exc).__name__}")
        except Exception as exc:
            print(f"Reminder loop error: {type(exc).__name__}: {exc}")
        await asyncio.sleep(30)


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


async def main():
    if not TOKEN or TOKEN == "put_your_token_here":
        raise RuntimeError("Set BOT_TOKEN in .env")
    init()
    bot = Bot(TOKEN)
    task = asyncio.create_task(reminder_loop(bot))
    discovery_task = asyncio.create_task(meme_update_loop())
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        discovery_task.cancel()
        await asyncio.gather(task, discovery_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
