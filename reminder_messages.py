"""Temporary alert message lifecycle, separate from the single home screen."""
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "reminder_messages.sqlite3"
logger = logging.getLogger(__name__)
LIFETIME_SECONDS = 90


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS temporary_reminders (
        user_id INTEGER PRIMARY KEY,
        message_id INTEGER NOT NULL,
        expires_at TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def remember(user_id, message_id, now=None):
    moment = now or datetime.now(timezone.utc)
    expiry = (moment + timedelta(seconds=LIFETIME_SECONDS)).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO temporary_reminders(user_id,message_id,expires_at) "
            "VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "message_id=excluded.message_id,expires_at=excluded.expires_at",
            (user_id, message_id, expiry),
        )


def previous(user_id):
    with connect() as conn:
        return conn.execute(
            "SELECT message_id FROM temporary_reminders WHERE user_id=?",
            (user_id,),
        ).fetchone()


async def remove(bot, uid, message_id):
    try:
        await bot.delete_message(uid, message_id)
    except Exception as exc:
        logger.info("Temporary reminder cleanup user=%s: %s", uid, type(exc).__name__)
    finally:
        with connect() as conn:
            conn.execute(
                "DELETE FROM temporary_reminders WHERE user_id=? AND message_id=?",
                (uid, message_id),
            )


async def cleanup_expired(bot, now=None):
    moment = (now or datetime.now(timezone.utc)).isoformat()
    with connect() as conn:
        expired = conn.execute(
            "SELECT user_id,message_id FROM temporary_reminders WHERE expires_at<=?",
            (moment,),
        ).fetchall()
    for record in expired:
        await remove(bot, record["user_id"], record["message_id"])


async def replace_existing(bot, uid):
    row = previous(uid)
    if row:
        await remove(bot, uid, row["message_id"])
