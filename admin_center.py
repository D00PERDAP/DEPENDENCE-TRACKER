"""Admin control center. Separate additive tables; never alter user health records."""
import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from aiogram import F
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

DB = Path(__file__).with_name("data") / "admin_center.sqlite3"

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT, actor INTEGER, action TEXT, target TEXT);
    CREATE TABLE IF NOT EXISTS announcements(id INTEGER PRIMARY KEY, created TEXT, actor INTEGER, body TEXT, sent INTEGER DEFAULT 0, failed INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS event_joins(event_id INTEGER NOT NULL, user_id INTEGER NOT NULL, joined TEXT NOT NULL, PRIMARY KEY(event_id,user_id));
    CREATE TABLE IF NOT EXISTS hidden_treasures(treasure_id INTEGER PRIMARY KEY, actor INTEGER, hidden_at TEXT);
    CREATE TABLE IF NOT EXISTS disabled_meme_characters(name TEXT PRIMARY KEY, actor INTEGER, disabled_at TEXT);
    """)
    c.commit()
    return c

def log(actor, action, target=""):
    with db() as c:
        c.execute("INSERT INTO audit(at,actor,action,target) VALUES(?,?,?,?)", (now(),actor,action,str(target)[:100]))

def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t,d in row] for row in rows])

class AdminFlow(StatesGroup):
    broadcast = State()
    event_title = State()
    event_description = State()

async def show(q, text, rows):
    import ui_manager
    return await ui_manager.render_text(q.message, text, kb(rows))

def install(dp, connect, is_admin, pirate):
    def allowed(uid):
        return is_admin(uid)

    @dp.callback_query(F.data == "ac:menu")
    async def menu(q):
        # Legacy button compatibility: a single dashboard now lives in bot.py.
        if not allowed(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        await q.answer()
        await show(q, "🛠 Э, вацок, штаб теперь один! Жми кнопку ниже.", [
            [("🛠 Единая админка", "admin:panel")]
        ])

    @dp.callback_query(F.data == "ac:stats")
    async def stats(q):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа", show_alert=True)
        await q.answer()
        with connect() as c:
            users=c.execute("SELECT COUNT(*) FROM bot_user_profiles").fetchone()[0]
            active=c.execute("SELECT COUNT(*) FROM bot_user_profiles WHERE last_seen >= datetime('now','-7 days')").fetchone()[0]
            sessions=c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            meme_count=c.execute("SELECT COUNT(*) FROM meme_sends").fetchone()[0]
        with pirate.db() as c:
            treasures=c.execute("SELECT COUNT(*) FROM treasures").fetchone()[0]
            banned=c.execute("SELECT COUNT(*) FROM banned_players").fetchone()[0]
        await show(q, f"🦅 МАГА | РАСКЛАДЫ ШТАБА\n\n👥 Вацоков: {users}\n🟢 Были за неделю: {active}\n💀 Сопровождений: {sessions}\n😂 Мемов отправлено: {meme_count}\n🏴‍☠️ Игровых сокровищ: {treasures}\n🚫 Блокировок: {banned}\n\nЛичные записи не показываем.", [[("⬅️ В штаб, брат","admin:panel")]])

    @dp.callback_query(F.data.startswith("ac:treasures:"))
    async def treasures(q):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа", show_alert=True)
        await q.answer()
        try: page=max(0,int(q.data.split(":")[-1]))
        except ValueError: page=0
        with pirate.db() as c:
            rows=c.execute("SELECT id,title FROM treasures ORDER BY id DESC LIMIT 8 OFFSET ?",(page*8,)).fetchall()
            total=c.execute("SELECT COUNT(*) FROM treasures").fetchone()[0]
        with db() as c:
            hidden={r[0] for r in c.execute("SELECT treasure_id FROM hidden_treasures")}
        buttons=[[(("🚫 " if r["id"] in hidden else "🗺️ ")+r["title"][:35],f"ac:treasure:{r['id']}")] for r in rows]
        nav=[]
        if page: nav.append(("⬅️",f"ac:treasures:{page-1}"))
        if (page+1)*8<total: nav.append(("➡️",f"ac:treasures:{page+1}"))
        if nav: buttons.append(nav)
        buttons.append([("🛠 Штаб Маги","admin:panel")])
        await show(q,f"🏴‍☠️ ЛИЧНЫЕ ТОЧКИ: {total}\nСкрытые отмечены 🚫",buttons)

    @dp.callback_query(F.data.startswith("ac:treasure:"))
    async def treasure(q):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа", show_alert=True)
        await q.answer()
        tid=int(q.data.split(":")[-1])
        with pirate.db() as c:
            row=c.execute("SELECT id,user_id,title FROM treasures WHERE id=?",(tid,)).fetchone()
        if not row: return await show(q,"Сокровище не найдено.",[[("Назад","ac:treasures:0")]])
        with db() as c:
            hidden=c.execute("SELECT 1 FROM hidden_treasures WHERE treasure_id=?",(tid,)).fetchone()
        await show(q,f"🏴‍☠️ {row['title'][:80]}\nID: {tid}\nАвтор: {row['user_id']}\nСтатус: {'Скрыто' if hidden else 'Доступно'}",[
            [(("✅ Открыть" if hidden else "🚫 Скрыть"),f"ac:toggle:{tid}")],
            [("⬅️ Список","ac:treasures:0")]
        ])

    @dp.callback_query(F.data.startswith("ac:toggle:"))
    async def toggle(q):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа", show_alert=True)
        tid=int(q.data.split(":")[-1])
        with pirate.db() as c:
            exists=c.execute("SELECT 1 FROM treasures WHERE id=?",(tid,)).fetchone()
        if not exists: return await q.answer("Не найдено",show_alert=True)
        with db() as c:
            hidden=c.execute("SELECT 1 FROM hidden_treasures WHERE treasure_id=?",(tid,)).fetchone()
            if hidden: c.execute("DELETE FROM hidden_treasures WHERE treasure_id=?",(tid,))
            else: c.execute("INSERT INTO hidden_treasures VALUES(?,?,?)",(tid,q.from_user.id,now()))
        log(q.from_user.id,"treasure_unhide" if hidden else "treasure_hide",tid)
        await treasure(q)

    @dp.callback_query(F.data == "ac:memes")
    async def memes(q):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа",show_alert=True)
        await q.answer()
        with connect() as c:
            names=[r[0] for r in c.execute("SELECT name FROM meme_characters ORDER BY name LIMIT 20")] if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meme_characters'").fetchone() else []
        with db() as c:
            disabled={r[0] for r in c.execute("SELECT name FROM disabled_meme_characters")}
        rows=[[(("🔴 " if name in disabled else "🟢 ")+name[:35],f"ac:meme:{i}")] for i,name in enumerate(names)]
        rows += [[("📁 Все коллекции","admin:collections")],[("⬅️ Мемный центр","admin:memehub")]]
        await show(q,"🦅 МАГА | МЕМНЫЙ КОНТРОЛЬ\n\nВыбирай персонажа, вацок.\nВажно: эти отметки пока не влияют на выдачу мемов.",rows)

    @dp.callback_query(F.data.startswith("ac:meme:"))
    async def meme_toggle(q):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа",show_alert=True)
        idx=int(q.data.split(":")[-1])
        with connect() as c:
            names=[r[0] for r in c.execute("SELECT name FROM meme_characters ORDER BY name LIMIT 20")]
        if idx>=len(names): return await q.answer("Не найдено",show_alert=True)
        name=names[idx]
        with db() as c:
            disabled=c.execute("SELECT 1 FROM disabled_meme_characters WHERE name=?",(name,)).fetchone()
            if disabled: c.execute("DELETE FROM disabled_meme_characters WHERE name=?",(name,))
            else: c.execute("INSERT INTO disabled_meme_characters VALUES(?,?,?)",(name,q.from_user.id,now()))
        log(q.from_user.id,"meme_enable" if disabled else "meme_disable",name)
        await memes(q)

    @dp.callback_query(F.data == "ac:audit")
    async def audit(q):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа",show_alert=True)
        await q.answer()
        with db() as c: rows=c.execute("SELECT at,actor,action,target FROM audit ORDER BY id DESC LIMIT 12").fetchall()
        lines=[f"{r['at'][:16]} · {r['actor']} · {r['action']} · {r['target']}" for r in rows]
        await show(q,"🛡 КТО ЧТО ДЕЛАЛ\n"+"\n".join(lines or ["Пока пусто"]),[[("⬅️ Обратно, брат","admin:panel")]])

    @dp.callback_query(F.data == "ac:broadcast")
    async def broadcast(q,state):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа",show_alert=True)
        await q.answer()
        await state.set_state(AdminFlow.broadcast)
        await show(q,"📢 ПЕРЕДАЁМ САЛАМ\nОтправь текст объявления (до 1000 символов). Сначала будет предпросмотр.",[[("⬅️ Обратно, брат","admin:panel")]])

    @dp.message(AdminFlow.broadcast)
    async def broadcast_text(m,state):
        if not allowed(m.from_user.id): return
        body=(m.text or "").strip()
        try: await m.delete()
        except Exception: pass
        if not body or len(body)>1000: return await m.answer("Текст от 1 до 1000 символов.")
        await state.clear()
        await state.update_data(broadcast=body)
        await m.answer("📢 ПРЕДПРОСМОТР\n\n"+body,reply_markup=kb([[("✅ Отправить","ac:send")],[("❌ Отмена","admin:panel")]]))

    @dp.callback_query(F.data == "ac:send")
    async def send(q,state):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа",show_alert=True)
        data=await state.get_data()
        body=data.get("broadcast")
        if not body: return await q.answer("Сначала подготовь объявление",show_alert=True)
        await state.clear()
        await q.answer("Рассылка запущена")
        with connect() as c: ids=[r[0] for r in c.execute("SELECT user_id FROM bot_user_profiles")]
        with db() as c:
            cursor=c.execute("INSERT INTO announcements(created,actor,body) VALUES(?,?,?)",(now(),q.from_user.id,body))
            announcement_id=cursor.lastrowid
        sent=failed=0
        for uid in ids:
            try:
                await q.bot.send_message(uid,"📢 Объявление\n\n"+body)
                sent+=1
            except Exception:
                failed+=1
            await asyncio.sleep(0.06)
        with db() as c: c.execute("UPDATE announcements SET sent=?,failed=? WHERE id=?",(sent,failed,announcement_id))
        log(q.from_user.id,"broadcast",f"{announcement_id}: {sent}/{failed}")
        await show(q,f"📢 Рассылка завершена\nДоставлено: {sent}\nОшибок: {failed}",[[("⬅️ Обратно, брат","admin:panel")]])

    @dp.callback_query(F.data == "ac:events")
    async def events(q):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа",show_alert=True)
        await q.answer()
        with db() as c: rows=c.execute("SELECT id,title,active FROM events ORDER BY id DESC LIMIT 12").fetchall()
        buttons=[[(("🟢 " if r["active"] else "⚪ ")+r["title"][:35],f"ac:event:{r['id']}")] for r in rows]
        buttons += [[("➕ Новое событие","ac:eventnew")],[("⬅️ Обратно, брат","admin:panel")]]
        await show(q,"🎮 СОБЫТИЯ\nПубликуются через отдельную кнопку в меню.",buttons)

    @dp.callback_query(F.data == "ac:eventnew")
    async def eventnew(q,state):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа",show_alert=True)
        await q.answer()
        await state.set_state(AdminFlow.event_title)
        await show(q,"Название события (до 60 символов):",[[("⬅️ Обратно, брат","ac:events")]])

    @dp.message(AdminFlow.event_title)
    async def eventtitle(m,state):
        if not allowed(m.from_user.id): return
        title=(m.text or "").strip()
        try: await m.delete()
        except Exception: pass
        if not 1<=len(title)<=60: return await m.answer("Название от 1 до 60 символов.")
        await state.update_data(event_title=title)
        await state.set_state(AdminFlow.event_description)
        await m.answer("Описание события (до 500 символов):")

    @dp.message(AdminFlow.event_description)
    async def eventdescription(m,state):
        if not allowed(m.from_user.id): return
        description=(m.text or "").strip()
        try: await m.delete()
        except Exception: pass
        if not 1<=len(description)<=500: return await m.answer("Описание от 1 до 500 символов.")
        data=await state.get_data()
        with db() as c: c.execute("INSERT INTO events(title,description,active,created) VALUES(?,?,0,?)",(data["event_title"],description,now()))
        await state.clear()
        log(m.from_user.id,"event_create",data["event_title"])
        await m.answer("🎮 Событие создано как черновик.",reply_markup=kb([[("🎮 События","ac:events")]]))

    @dp.callback_query(F.data.startswith("ac:event:"))
    async def event(q):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа",show_alert=True)
        await q.answer()
        eid=int(q.data.split(":")[-1])
        with db() as c:
            row=c.execute("SELECT * FROM events WHERE id=?",(eid,)).fetchone()
            joined=c.execute("SELECT COUNT(*) FROM event_joins WHERE event_id=?",(eid,)).fetchone()[0]
        if not row: return await show(q,"Событие не найдено",[[("Назад","ac:events")]])
        await show(q,f"🎮 {row['title']}\n\n{row['description']}\nУчастников: {joined}\nСтатус: {'Открыто' if row['active'] else 'Закрыто'}",[
            [(("⏹ Закрыть" if row["active"] else "▶️ Открыть"),f"ac:eventtoggle:{eid}")],[("⬅️ События","ac:events")]])

    @dp.callback_query(F.data.startswith("ac:eventtoggle:"))
    async def eventtoggle(q):
        if not allowed(q.from_user.id): return await q.answer("Нет доступа",show_alert=True)
        eid=int(q.data.split(":")[-1])
        with db() as c: c.execute("UPDATE events SET active=1-active WHERE id=?",(eid,))
        log(q.from_user.id,"event_toggle",eid)
        await event(q)

    @dp.message(F.text == "🎮 События")
    async def public_events(m):
        with db() as c: rows=c.execute("SELECT id,title FROM events WHERE active=1 ORDER BY id DESC LIMIT 10").fetchall()
        buttons=[[(r["title"][:40],f"ev:detail:{r['id']}")] for r in rows]
        await m.answer("🎮 Активные события" if rows else "Сейчас нет активных событий.",reply_markup=kb(buttons))

    @dp.callback_query(F.data.startswith("ev:detail:"))
    async def public_event(q):
        await q.answer()
        eid=int(q.data.split(":")[-1])
        with db() as c: row=c.execute("SELECT * FROM events WHERE id=? AND active=1",(eid,)).fetchone()
        if not row: return await q.answer("Событие закрыто",show_alert=True)
        await show(q,f"🎮 {row['title']}\n\n{row['description']}",[[("✅ Участвовать",f"ev:join:{eid}")]])

    @dp.callback_query(F.data.startswith("ev:join:"))
    async def join(q):
        eid=int(q.data.split(":")[-1])
        with db() as c:
            row=c.execute("SELECT 1 FROM events WHERE id=? AND active=1",(eid,)).fetchone()
            if not row: return await q.answer("Событие закрыто",show_alert=True)
            c.execute("INSERT OR IGNORE INTO event_joins VALUES(?,?,?)",(eid,q.from_user.id,now()))
        await q.answer("Ты участвуешь!",show_alert=True)
