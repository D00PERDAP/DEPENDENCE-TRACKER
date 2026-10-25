"""Private, fictional game-reward inventory; no geographic coordinates or personal health data.

Admin grants titled items (optional picture) to a registered player. Telegram
notification is deleted on acknowledgement, but the awarded item is retained.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aiogram import F
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

DB = Path(__file__).resolve().parent / "data" / "game_rewards.sqlite3"
logger = logging.getLogger(__name__)


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS rewards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        admin_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        photo_id TEXT,
        created_at TEXT NOT NULL,
        acknowledged_at TEXT,
        notice_message_id INTEGER
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rewards_user ON rewards(user_id,id DESC)")
    c.commit()
    return c


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=callback)
         for label, callback in row] for row in rows
    ])


def grant(user_id, admin_id, title, photo_id=None):
    if not 1 <= len(title.strip()) <= 80:
        raise ValueError("Item name must be 1–80 characters")
    with connect() as c:
        cur = c.execute(
            "INSERT INTO rewards(user_id,admin_id,title,photo_id,created_at) "
            "VALUES(?,?,?,?,?)",
            (user_id, admin_id, title.strip(), photo_id,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def inventory(user_id):
    with connect() as c:
        return c.execute(
            "SELECT * FROM rewards WHERE user_id=? ORDER BY id DESC LIMIT 30",
            (user_id,),
        ).fetchall()


class RewardFlow(StatesGroup):
    title = State()
    optional_photo = State()


def install(dp, *, is_admin, user_exists, render_text):
    async def show_admin(q, uid):
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        if not user_exists(uid):
            return await q.answer("Игрок не найден", show_alert=True)
        rows = inventory(uid)
        lines = [
            f"• #{r['id']} · {r['title']}"
            + (" · ✅" if r["acknowledged_at"] else " · ⏳")
            for r in rows
        ]
        text = ("🏴‍☠️ МАГА | НАГРАДЫ ИГРОКА\n\n"
                f"Игрок: {uid}\n"
                f"Выдано: {len(rows)}\n\n"
                + ("\n".join(lines) if lines else "Пока пусто, брат."))
        await render_text(q.message, text, keyboard([
            [("🎁 Выдать игровую награду", f"rw:grant:{uid}")],
            [("⬅️ К игроку", f"admin:user:{uid}")],
        ]))

    @dp.callback_query(F.data.startswith("rw:admin:"))
    async def admin_inventory(q):
        await q.answer()
        await show_admin(q, int(q.data.rsplit(":", 1)[-1]))

    @dp.callback_query(F.data.startswith("rw:grant:"))
    async def start_grant(q, state):
        uid = int(q.data.rsplit(":", 1)[-1])
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        if not user_exists(uid):
            return await q.answer("Игрок не найден", show_alert=True)
        await q.answer()
        await state.set_state(RewardFlow.title)
        await state.update_data(reward_target=uid)
        await render_text(q.message,
            "🎁 МАГА | ВЫДАЧА НАГРАДЫ\n\n"
            "Напиши название игрового сокровища, вацок. До 80 символов.\n"
            "Для отмены введи /cancelreward.",
            keyboard([[("⬅️ Назад к игроку", f"rw:admin:{uid}")]]))

    @dp.message(RewardFlow.title)
    async def receive_title(m, state):
        if not is_admin(m.from_user.id):
            await state.clear()
            return
        name = (m.text or "").strip()
        if not 1 <= len(name) <= 80:
            await m.answer("Название должно быть от 1 до 80 символов, брат.")
            return
        await state.update_data(reward_title=name)
        await state.set_state(RewardFlow.optional_photo)
        await m.answer("📸 Можешь отправить фотографию награды. "
                       "Или напиши «без фото», брат.")

    @dp.message(RewardFlow.optional_photo)
    async def receive_photo(m, state):
        if not is_admin(m.from_user.id):
            await state.clear()
            return
        skip = (m.text or "").strip().casefold() == "без фото"
        if not skip and not m.photo:
            await m.answer("Отправь фото или напиши «без фото», вацок.")
            return
        data = await state.get_data()
        uid = data.get("reward_target")
        title = data.get("reward_title")
        await state.clear()
        if not isinstance(uid, int) or not title or not user_exists(uid):
            return await m.answer("Игрок не найден. Награда не выдана.")
        photo_id = m.photo[-1].file_id if m.photo else None
        rid = grant(uid, m.from_user.id, title, photo_id)
        notice = (f"🏴‍☠️ ВАЦОК, ТЕБЕ НАГРАДА!\n\n"
                  f"🎁 {title}\n\nМага передал. Забирай в игровую коллекцию!")
        markup = keyboard([[("✅ Окей, брат, понял!", f"rw:ack:{rid}")]])
        try:
            # Player gets one notification. No secret location is transmitted.
            sent = (await m.bot.send_photo(uid, photo_id, caption=notice,
                                           reply_markup=markup)
                    if photo_id else
                    await m.bot.send_message(uid, notice, reply_markup=markup))
            with connect() as c:
                c.execute("UPDATE rewards SET notice_message_id=? WHERE id=?",
                          (sent.message_id, rid))
            await m.answer(f"✅ Награда #{rid} выдана! Игроку пришло уведомление.")
        except Exception as exc:
            logger.warning("Cannot send game reward notification %s: %s", rid, exc)
            await m.answer(f"⚠️ Награда #{rid} сохранена, но сообщение игроку "
                           "не доставлено. Проверь, запускал ли он бота.")

    @dp.callback_query(F.data.startswith("rw:ack:"))
    async def acknowledge(q):
        try:
            rid = int(q.data.rsplit(":", 1)[-1])
        except ValueError:
            return await q.answer("Награда не найдена", show_alert=True)
        with connect() as c:
            row = c.execute(
                "SELECT user_id,acknowledged_at,notice_message_id FROM rewards WHERE id=?",
                (rid,),
            ).fetchone()
            if not row or row["user_id"] != q.from_user.id:
                return await q.answer("Это не твоё уведомление", show_alert=True)
            if not row["acknowledged_at"]:
                c.execute("UPDATE rewards SET acknowledged_at=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"),rid))
        await q.answer("Жи есть, брат! Награда твоя.")
        try:
            await q.message.delete()
        except Exception as exc:
            logger.info("Reward notification could not be deleted: %s", exc)
            try:
                await q.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

    @dp.callback_query(F.data == "rw:mine")
    async def my_rewards(q):
        await q.answer()
        rows = inventory(q.from_user.id)
        text = ("🎁 МАГА | МОИ СОКРОВИЩА\n\n"
                + ("\n".join(f"• {r['title']}" for r in rows)
                   if rows else "Пока пусто, вацок."))
        await render_text(q.message,text,keyboard([
            [("⚙️ Настройки", "ui:mine")],
            [("🏠 На базу", "v2:home")],
        ]))

    @dp.message(F.text == "/cancelreward")
    async def cancel_reward(m, state):
        current = await state.get_state()
        if current and current.startswith("RewardFlow:"):
            await state.clear()
            await m.answer("Выдача отменена, брат.")
