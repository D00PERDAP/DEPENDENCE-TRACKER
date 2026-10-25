"""Private, fictional game-reward inventory; no geographic coordinates or personal health data.

Admin grants titled items (optional picture) to a registered player. Telegram
notification is deleted on acknowledgement, but the awarded item is retained.
"""
from __future__ import annotations

import logging
import re
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
        notice_message_id INTEGER,
        latitude REAL,
        longitude REAL
    )""")
    columns = {r["name"] for r in c.execute("PRAGMA table_info(rewards)")}
    for name in ("latitude", "longitude"):
        if name not in columns:
            c.execute(f"ALTER TABLE rewards ADD COLUMN {name} REAL")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rewards_user ON rewards(user_id,id DESC)")
    c.commit()
    return c


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=callback)
         for label, callback in row] for row in rows
    ])


def parse_coordinates(text):
    """Accept decimal GPS pairs; for example 55.751244, 37.618423."""
    parts = re.split(r"[,;\s]+", (text or "").strip())
    if len(parts) != 2:
        raise ValueError("Two coordinates required")
    try:
        lat, lon = map(float, parts)
    except ValueError as exc:
        raise ValueError("Invalid GPS numbers") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("GPS out of bounds")
    return lat, lon


def grant(user_id, admin_id, title, photo_id=None, latitude=None, longitude=None):
    if not 1 <= len(title.strip()) <= 80:
        raise ValueError("Item name must be 1–80 characters")
    if (latitude is None) != (longitude is None):
        raise ValueError("Both GPS coordinates required")
    if latitude is not None and not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Invalid GPS coordinates")
    with connect() as c:
        cur = c.execute(
            "INSERT INTO rewards(user_id,admin_id,title,photo_id,created_at,latitude,longitude) "
            "VALUES(?,?,?,?,?,?,?)",
            (user_id, admin_id, title.strip(), photo_id,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             latitude, longitude),
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
    location = State()


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
        text = ("🏴‍☠️ МАГА | КЛАДЫ ВАЦОКА\n\n"
                f"Игрок: {uid}\n"
                f"Выдано: {len(rows)}\n\n"
                + ("\n".join(lines) if lines else "Пока пусто, брат."))
        choices = [[(f"🗺️ #{r['id']} · {r['title'][:30]}", f"rw:adminview:{r['id']}")]
                   for r in rows]
        choices += [
            [("🏴‍☠️ Закинуть клад, брат", f"rw:grant:{uid}")],
            [("⬅️ К игроку", f"admin:user:{uid}")],
        ]
        await render_text(q.message, text, keyboard(choices))

    @dp.callback_query(F.data.startswith("rw:adminview:"))
    async def admin_view(q):
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        rid = int(q.data.rsplit(":", 1)[-1])
        with connect() as c:
            item = c.execute("SELECT * FROM rewards WHERE id=?", (rid,)).fetchone()
        if not item:
            return await q.answer("Клад не найден, брат.", show_alert=True)
        await q.answer()
        coords = (f"{item['latitude']:.7f}, {item['longitude']:.7f}"
                  if item["latitude"] is not None else "не указаны")
        await render_text(
            q.message,
            f"🏴‍☠️ МАГА | КЛАД #{rid}\n\n"
            f"👤 Игрок: {item['user_id']}\n"
            f"🎁 {item['title']}\n"
            f"📍 Корды, брат: {coords}\n"
            f"📸 Фотка: {'есть' if item['photo_id'] else 'нет'}\n"
            f"✅ Подтверждено: {'да' if item['acknowledged_at'] else 'ещё нет'}",
            keyboard([[("⬅️ К кладам игрока", f"rw:admin:{item['user_id']}")]]),
        )

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
            "🏴‍☠️ МАГА | ВЫДАЧА КЛАДА\n\n"
            "1/3 · Как назовём клад, вацок? До 80 символов.\n"
            "Отбой — /cancelreward.",
            keyboard([[("⬅️ Назад к игроку", f"rw:admin:{uid}")]]))

    @dp.message(RewardFlow.title)
    async def receive_title(m, state):
        if not is_admin(m.from_user.id):
            await state.clear()
            return
        if (m.text or "").strip() == "/cancelreward":
            await state.clear()
            return await m.answer("Выдача отменена, брат.")
        name = (m.text or "").strip()
        if not 1 <= len(name) <= 80:
            await m.answer("Название должно быть от 1 до 80 символов, брат.")
            return
        await state.update_data(reward_title=name)
        await state.set_state(RewardFlow.optional_photo)
        await m.answer("📸 2/3 · Давай фотку клада, брат, жи есть! "
                       "Или напиши «без фото», брат.")

    @dp.message(RewardFlow.optional_photo)
    async def receive_photo(m, state):
        if not is_admin(m.from_user.id):
            await state.clear()
            return
        if (m.text or "").strip() == "/cancelreward":
            await state.clear()
            return await m.answer("Выдача отменена, брат.")
        skip = (m.text or "").strip().casefold() == "без фото"
        if not skip and not m.photo:
            await m.answer("Отправь фото или напиши «без фото», вацок.")
            return
        await state.update_data(reward_photo=m.photo[-1].file_id if m.photo else None)
        await state.set_state(RewardFlow.location)
        await m.answer(
            "📍 3/3 · Давай корды, брат, жи есть!\n\n"
            "Пришли геолокацию Telegram или два GPS-числа:\n"
            "55.751244, 37.618423\n\n"
            "Выбирай безопасное общественное место."
        )

    @dp.message(RewardFlow.location)
    async def receive_location(m, state):
        if not is_admin(m.from_user.id):
            await state.clear()
            return
        if (m.text or "").strip() == "/cancelreward":
            await state.clear()
            return await m.answer("Отбой, брат. Выдачу отменили.")
        try:
            if m.location:
                lat, lon = m.location.latitude, m.location.longitude
            else:
                lat, lon = parse_coordinates(m.text)
        except ValueError:
            return await m.answer(
                "Не разобрал корды, вацок. Пришли геолокацию или "
                "два числа: 55.751244, 37.618423"
            )
        data = await state.get_data()
        uid, title = data.get("reward_target"), data.get("reward_title")
        photo_id = data.get("reward_photo")
        if not isinstance(uid, int) or not title or not user_exists(uid):
            await state.clear()
            return await m.answer("Вацок не найден, клад не выдавался.")
        rid = grant(uid, m.from_user.id, title, photo_id, lat, lon)
        await state.clear()
        notice = (
            "🏴‍☠️ МАГА | ТЕБЕ КЛАД, ВАЦОК!\n\n"
            f"🎁 {title}\n\n"
            "Мага закинул новую игровую точку, жи есть!\n"
            "📍 Корды и фотка ждут в «Мои сокровища».\n"
            "Жми «Окей» — уведомление уйдёт, клад останется."
        )
        markup = keyboard([[("✅ Окей, брат, понял!", f"rw:ack:{rid}")]])
        try:
            sent = (await m.bot.send_photo(uid, photo_id, caption=notice,
                                           reply_markup=markup)
                    if photo_id else
                    await m.bot.send_message(uid, notice, reply_markup=markup))
            with connect() as c:
                c.execute("UPDATE rewards SET notice_message_id=? WHERE id=?",
                          (sent.message_id, rid))
            await m.answer(
                f"✅ Жи есть! Клад #{rid} выдан игроку {uid}.\n"
                "Уведомление ушло, корды сохранены."
            )
        except Exception as exc:
            logger.warning("Cannot send game reward notification %s: %s", rid, exc)
            await m.answer(
                f"⚠️ Клад #{rid} сохранён, но уведомление не доставлено.\n"
                "Проверь, запускал ли вацок бота."
            )

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
        buttons = [[(f"🗺️ {r['title'][:38]}", f"rw:view:{r['id']}")] for r in rows]
        buttons += [
            [("⚙️ Настройки", "ui:mine")],
            [("🏠 На базу", "v2:home")],
        ]
        await render_text(
            q.message,
            "🏴‍☠️ МАГА | МОИ КЛАДЫ\n\n"
            + (f"На счету {len(rows)} игровых тайников, вацок.\n"
               "Жми на нужный — там корды и фотка."
               if rows else "Пока пусто, брат. Ждём игровой квест!"),
            keyboard(buttons),
        )

    @dp.callback_query(F.data.startswith("rw:view:"))
    async def view_reward(q):
        rid = int(q.data.rsplit(":", 1)[-1])
        with connect() as c:
            reward = c.execute(
                "SELECT * FROM rewards WHERE id=? AND user_id=?",
                (rid, q.from_user.id),
            ).fetchone()
        if not reward:
            return await q.answer("Это не твой клад, вацок.", show_alert=True)
        await q.answer()
        coords = (
            f"📍 Твои корды, вацок: "
            f"{reward['latitude']:.7f}, {reward['longitude']:.7f}"
            if reward["latitude"] is not None else
            "📍 Корды ещё не заданы."
        )
        rows = []
        if reward["photo_id"]:
            rows.append([("📸 Твоя фотка, брат", f"rw:photo:{rid}")])
        rows.append([("⬅️ К моим кладам", "rw:mine")])
        await render_text(
            q.message,
            f"🏴‍☠️ МАГА | {reward['title']}\n\n"
            f"{coords}\n\n"
            + ("📸 Фотка есть, жми ниже."
               if reward["photo_id"] else "📸 Без фотки, брат."),
            keyboard(rows),
        )

    @dp.callback_query(F.data.startswith("rw:photo:"))
    async def reward_photo(q):
        rid = int(q.data.rsplit(":", 1)[-1])
        with connect() as c:
            reward = c.execute(
                "SELECT * FROM rewards WHERE id=? AND user_id=?",
                (rid, q.from_user.id),
            ).fetchone()
        if not reward or not reward["photo_id"]:
            return await q.answer("Фотка не найдена, брат.", show_alert=True)
        await q.answer()
        await q.message.answer_photo(
            reward["photo_id"],
            caption=f"📸 Твоя фотка, вацок!\n🏴‍☠️ {reward['title']}",
            reply_markup=keyboard([[("⬅️ К кладу", f"rw:view:{rid}")]]),
        )

    @dp.message(F.text == "/cancelreward")
    async def cancel_reward(m, state):
        current = await state.get_state()
        if current and current.startswith("RewardFlow:"):
            await state.clear()
            await m.answer("Выдача отменена, брат.")
