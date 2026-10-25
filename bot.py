import asyncio
import os
import sqlite3
import secrets
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, BufferedInputFile, InputMediaPhoto
from dotenv import load_dotenv
from meme_cards import card
from memes import streak_caption, session_caption, completed_goal_caption, ended_session_caption

load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
DB = Path(__file__).with_name('tracker.db')
SUBSTANCES = ['Мефедрон', 'Альфа-ПВП', 'Амфетамин', 'Метамфетамин', 'Каннабис', 'MDMA', 'Кокаин', 'Опиоиды', 'Бензодиазепины', 'ЛСД', 'Псилоцибин', 'Другое']
GOALS = ['Не смешивать вещества', 'Не садиться за руль', 'Быть с человеком, которому доверяю', 'Проверять самочувствие', 'Не оставаться одному при ухудшении состояния']

class Form(StatesGroup):
    note = State()
    goal = State()

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, substance TEXT);
        CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, substance TEXT NOT NULL, started TEXT NOT NULL, ended TEXT);
        CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, at TEXT NOT NULL, note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS goals (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, title TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0);
        ''')

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=v) for t,v in row] for row in rows])

def main_kb():
    return keyboard([[('🎲 Мем','meme'),('📝 Записать','session')], [('📊 Мой прогресс','stats'), ('⚙️ Ещё','more')]])

def active(c, uid):
    return c.execute('SELECT * FROM sessions WHERE user_id=? AND ended IS NULL ORDER BY id DESC LIMIT 1', (uid,)).fetchone()

def selected(c, uid):
    row = c.execute('SELECT substance FROM users WHERE user_id=?', (uid,)).fetchone()
    return row['substance'] if row else None

async def show(message, text, kb=None):
    try:
        if message.photo:
            await message.delete()
            await message.answer(text, reply_markup=kb)
        else:
            await message.edit_text(text, reply_markup=kb)
    except Exception:
        await message.answer(text, reply_markup=kb)

async def send_card(message, uid, menu=False):
    with conn() as c:
        last = c.execute('SELECT substance, started FROM sessions WHERE user_id=? ORDER BY started DESC LIMIT 1', (uid,)).fetchone()
        sub = selected(c, uid)
        current = active(c, uid)
    days = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(last['started'])).days) if last else -1
    variant = secrets.randbelow(10000)
    subtitle = ('🔴 Идёт запись сессии.' if current else
                f'🗿 {days} дн. с последней записи в журнале.' if last else
                '🗿 Начни с записи или просто лови мем.')
    subtitle += '\\nСчётчик основан только на твоих записях.'
    kb = main_kb() if menu else keyboard([[('🎲 Другой мем','meme')], [('🏠 Меню','home')]])
    try:
        picture = await asyncio.to_thread(card, days, last['substance'] if last else (sub or 'вещества'), variant)
        if getattr(message, 'photo', None):
            try:
                await message.edit_media(InputMediaPhoto(media=BufferedInputFile(picture, filename='meme.png'), caption=subtitle), reply_markup=kb)
                return
            except Exception:
                pass
        await message.answer_photo(BufferedInputFile(picture, filename='meme.png'), caption=subtitle, reply_markup=kb)
    except Exception:
        await message.answer('🗿 НЕ СЕГОДНЯ\\n' + subtitle, reply_markup=kb)

async def home(message, uid):
    await send_card(message, uid, menu=True)

bot_dp = Dispatcher()

@bot_dp.message(CommandStart())
async def start(m:Message):
    await home(m,m.from_user.id)

@bot_dp.callback_query(F.data == 'home')
async def back(q:CallbackQuery):
    await q.answer()
    await home(q.message,q.from_user.id)

@bot_dp.callback_query(F.data == 'meme')
async def meme(q:CallbackQuery):
    await q.answer()
    await send_card(q.message, q.from_user.id)

@bot_dp.callback_query(F.data == 'more')
async def more(q:CallbackQuery):
    await q.answer()
    await show(q.message, '⚙️ ЕЩЁ\\nВсё полезное без десятка кнопок на главной.', keyboard([
        [('💊 Вещество','substances'), ('🎯 Цели','goals')],
        [('🛟 Безопасность','safety'), ('⚙️ Настройки','settings')],
        [('🏠 Меню','home')]
    ]))

@bot_dp.callback_query(F.data == 'substances')
async def substances(q:CallbackQuery):
    await q.answer()
    rows = [[(s, f'sub:{i}')] for i,s in enumerate(SUBSTANCES)] + [[('⬅️ Назад','home')]]
    await show(q.message,'💊 ВЫБЕРИ ВЕЩЕСТВО\nМожно сменить позже. У каждой записи сохраняется исходное вещество.',keyboard(rows))

@bot_dp.callback_query(F.data.startswith('sub:'))
async def set_sub(q:CallbackQuery):
    i = int(q.data.split(':')[1])
    if i not in range(len(SUBSTANCES)):
        return await q.answer('Неизвестный выбор',show_alert=True)
    with conn() as c:
        c.execute('INSERT INTO users(user_id,substance) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET substance=excluded.substance',(q.from_user.id,SUBSTANCES[i]))
    await q.answer('Выбрано')
    await home(q.message,q.from_user.id)

@bot_dp.callback_query(F.data == 'session')
async def session(q:CallbackQuery):
    await q.answer()
    with conn() as c:
        sub = selected(c,q.from_user.id)
        ses = active(c,q.from_user.id)
    if not sub:
        return await show(q.message,'Для записи выбери вещество.',keyboard([[('💊 Выбрать','substances')],[('🏠 Меню','home')]]))
    if ses:
        return await show(q.message,f'🔴 СЕССИЯ ОТКРЫТА\n{sub}\nНачало (UTC): {ses["started"]}\n\nЗаписывай самочувствие, не ориентируйся на таймер как на признак безопасности.',keyboard([[('📝 Добавить заметку','note')],[('🎯 Цели','goals')],[('⏹ Завершить сессию','end')],[('⬅️ Назад','home')]]))
    await show(q.message,f'📝 НОВАЯ СЕССИЯ: {sub}\n\nБот фиксирует время и заметки. Он не рассчитывает безопасные дозы.',keyboard([[('▶️ Начать запись','begin')],[('⬅️ Назад','home')]]))

@bot_dp.callback_query(F.data == 'begin')
async def begin(q:CallbackQuery):
    with conn() as c:
        sub=selected(c,q.from_user.id)
        if not sub:
            return await q.answer('Сначала выбери вещество',show_alert=True)
        if not active(c,q.from_user.id):
            c.execute('INSERT INTO sessions(user_id,substance,started) VALUES(?,?,?)',(q.from_user.id,sub,now()))
    await q.answer(session_caption())
    await session(q)

@bot_dp.callback_query(F.data == 'end')
async def end(q:CallbackQuery):
    with conn() as c:
        ses=active(c,q.from_user.id)
        if ses:
            c.execute('UPDATE sessions SET ended=? WHERE id=?',(now(),ses['id']))
    await q.answer(ended_session_caption())
    await home(q.message,q.from_user.id)

@bot_dp.callback_query(F.data == 'note')
async def note(q:CallbackQuery,state:FSMContext):
    with conn() as c:
        ses=active(c,q.from_user.id)
    if not ses:
        return await q.answer('Сначала начни сессию',show_alert=True)
    await state.set_state(Form.note)
    await q.answer()
    await show(q.message,'📝 Напиши одним сообщением, что произошло или как себя чувствуешь. /start — отмена.')

@bot_dp.message(Form.note)
async def save_note(m:Message,state:FSMContext):
    if not m.text or len(m.text)>1000:
        return await m.answer('Нужен текст до 1000 символов.')
    with conn() as c:
        ses=active(c,m.from_user.id)
        if ses:
            c.execute('INSERT INTO entries(session_id,at,note) VALUES(?,?,?)',(ses['id'],now(),m.text))
    await state.clear()
    await m.answer('🗿 ЗАПИСАНО. ПАМЯТЬ НЕ РЕЗИНОВАЯ, А SQLITE — ДА.',reply_markup=main_kb())

@bot_dp.callback_query(F.data == 'goals')
async def goals(q:CallbackQuery):
    with conn() as c:
        ses=active(c,q.from_user.id)
        saved=c.execute('SELECT * FROM goals WHERE session_id=?',(ses['id'],)).fetchall() if ses else []
    if not ses:
        return await q.answer('Для целей сначала начни сессию',show_alert=True)
    txt='🎯 ЦЕЛИ СЕССИИ\n\n'+'\n'.join(f'{"✅" if x["completed"] else "⬜"} {x["title"]}' for x in saved)
    if not saved: txt+='Пока нет целей.'
    rows=[[(f'{"☑️" if x["completed"] else "⬜"} {x["title"][:30]}',f'goal_toggle:{x["id"]}')] for x in saved]
    rows += [[('➕ Добавить цель','goal_add')],[('⬅️ Назад','session')]]
    await q.answer()
    await show(q.message,txt,keyboard(rows))

@bot_dp.callback_query(F.data == 'goal_add')
async def goal_add(q:CallbackQuery):
    await q.answer()
    await show(q.message,'🎯 ЦЕЛЬ — ЭТО ГРАНИЦА, А НЕ ЧЕЛЛЕНДЖ НА УПОТРЕБЛЕНИЕ.',keyboard([[(x,f'goal_preset:{i}')] for i,x in enumerate(GOALS)]+[[('✍️ Своя цель','goal_custom')],[('⬅️ Назад','goals')]]))

@bot_dp.callback_query(F.data.startswith('goal_preset:'))
async def goal_preset(q:CallbackQuery):
    i=int(q.data.split(':')[1]);
    if i not in range(len(GOALS)): return await q.answer('Неизвестная цель',show_alert=True)
    await insert_goal(q.from_user.id,GOALS[i]); await goals(q)

async def insert_goal(uid,title):
    with conn() as c:
        ses=active(c,uid)
        if ses: c.execute('INSERT INTO goals(session_id,title) VALUES(?,?)',(ses['id'],title))

@bot_dp.callback_query(F.data == 'goal_custom')
async def goal_custom(q:CallbackQuery,state:FSMContext):
    await state.set_state(Form.goal);await q.answer()
    await show(q.message,'✍️ Напиши свою цель одним сообщением. /start — отмена.')

@bot_dp.message(Form.goal)
async def save_goal(m:Message,state:FSMContext):
    if not m.text or len(m.text)>140: return await m.answer('Нужен текст до 140 символов.')
    await insert_goal(m.from_user.id,m.text)
    await state.clear();await m.answer('🎯 МИССИЯ ПРИНЯТА, КОМАНДИР.',reply_markup=main_kb())

@bot_dp.callback_query(F.data.startswith('goal_toggle:'))
async def goal_toggle(q:CallbackQuery):
    gid=int(q.data.split(':')[1])
    with conn() as c:
        ses=active(c,q.from_user.id)
        if ses:
            c.execute('UPDATE goals SET completed=1-completed WHERE id=? AND session_id=?',(gid,ses['id']))
    await q.answer(completed_goal_caption())
    await goals(q)

@bot_dp.callback_query(F.data == 'stats')
async def stats(q:CallbackQuery):
    with conn() as c:
        rows=c.execute('SELECT substance,started FROM sessions WHERE user_id=? ORDER BY started DESC',(q.from_user.id,)).fetchall()
    if not rows: txt='📊 СТАТИСТИКА\n\nПока нет записей. НИКТО НЕ РОЖДАЕТСЯ С EXCEL В ГОЛОВЕ.'
    else:
        last=datetime.fromisoformat(rows[0]['started']);days=(datetime.now(timezone.utc)-last).days
        txt=f'📊 СТАТИСТИКА\n\nВсего сессий: {len(rows)}\nС последней записи: {days} дн.\nПоследнее вещество: {rows[0]["substance"]}\n\nЭто период с последней записи, не медицинская оценка.'
    await q.answer();await show(q.message,txt,keyboard([[('⚙️ Ещё','more')], [('🏠 Меню','home')]]))

@bot_dp.callback_query(F.data == 'safety')
async def safety(q:CallbackQuery):
    await q.answer()
    await show(q.message,'🛟 БЕЗОПАСНОСТЬ\n\n⚠️ Сильная боль в груди, судороги, потеря сознания, затруднённое дыхание, перегрев или выраженная спутанность — повод немедленно вызвать экстренную помощь (112 в России).\n\nНе оставляй человека одного; при отсутствии сознания, но нормальном дыхании — положи на бок. При отсутствии нормального дыхания следуй указаниям диспетчера по реанимации.\n\nНе смешивай вещества. Состав и сила уличных веществ непредсказуемы. Таймер не означает, что стало безопасно.',keyboard([[('⬅️ Назад','home')]]))

@bot_dp.callback_query(F.data == 'settings')
async def settings(q:CallbackQuery):
    await q.answer();await show(q.message,'⚙️ НАСТРОЙКИ\n\nУдаление стирает записи из активной базы. Резервные копии сервера нужно удалять отдельно.',keyboard([[('🗑 Удалить все мои данные','delete_ask')],[('⬅️ Назад','home')]]))

@bot_dp.callback_query(F.data == 'delete_ask')
async def delete_ask(q:CallbackQuery):
    await q.answer();await show(q.message,'⚠️ ТОЧНО УДАЛИТЬ ВСЮ ИСТОРИЮ, ЦЕЛИ И НАСТРОЙКИ?\n\nПУТИН СПРАШИВАЕТ ПОСЛЕДНИЙ РАЗ.',keyboard([[('🗑 Да, удалить всё','delete_yes')],[('Нет, отмена','settings')]]))

@bot_dp.callback_query(F.data == 'delete_yes')
async def delete_yes(q:CallbackQuery):
    with conn() as c:
        c.execute('DELETE FROM entries WHERE session_id IN (SELECT id FROM sessions WHERE user_id=?)',(q.from_user.id,))
        c.execute('DELETE FROM goals WHERE session_id IN (SELECT id FROM sessions WHERE user_id=?)',(q.from_user.id,))
        c.execute('DELETE FROM sessions WHERE user_id=?',(q.from_user.id,))
        c.execute('DELETE FROM users WHERE user_id=?',(q.from_user.id,))
    await q.answer('Удалено');await show(q.message,'🗑 ВСЁ УДАЛЕНО ИЗ АКТИВНОЙ БАЗЫ.\n\nНачать заново: /start')

async def main():
    if not TOKEN or TOKEN=='put_your_token_here':
        raise RuntimeError('Укажи BOT_TOKEN в .env')
    init()
    bot=Bot(TOKEN)
    await bot_dp.start_polling(bot)

if __name__=='__main__':
    asyncio.run(main())
