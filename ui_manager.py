"""Single active private-chat screen shared by callbacks, memes and statistics.

A persistent pointer survives restarts. Telegram cannot morph a text message
into a photo (or vice versa), so incompatible types are replaced then cleaned up.
Scheduled safety reminders and private harbor messages are deliberately separate.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, FSInputFile, InputMediaPhoto, InputRichMessage

DB = Path(__file__).resolve().parent / "data" / "ui_screens.sqlite3"
_locks = {}


def _connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS screens (
        chat_id INTEGER PRIMARY KEY,
        message_id INTEGER NOT NULL,
        media_kind TEXT NOT NULL CHECK(media_kind IN ('text','photo'))
    )""")
    return c


def _get(chat_id):
    with _connection() as c:
        return c.execute("SELECT message_id,media_kind FROM screens WHERE chat_id=?", (chat_id,)).fetchone()


def _set(chat_id, message_id, media_kind):
    with _connection() as c:
        c.execute("INSERT INTO screens(chat_id,message_id,media_kind) VALUES(?,?,?) "
                  "ON CONFLICT(chat_id) DO UPDATE SET message_id=excluded.message_id, "
                  "media_kind=excluded.media_kind", (chat_id, message_id, media_kind))


def forget(chat_id):
    with _connection() as c:
        c.execute("DELETE FROM screens WHERE chat_id=?", (chat_id,))


def _lock(chat_id):
    # Locks are process-local; this bot must run as one polling process.
    return _locks.setdefault(chat_id, asyncio.Lock())


async def _remove(bot, chat_id, message_id):
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        # An old message may have been deleted, expired or be inaccessible.
        pass


def _same_message_error(exc):
    return "message is not modified" in str(exc).lower()


async def render_text(message, text, markup=None):
    """Edit the registered text screen or atomically replace the old media screen."""
    chat_id = message.chat.id
    async with _lock(chat_id):
        old = _get(chat_id)
        if old is None and getattr(message, "from_user", None) and message.from_user.is_bot:
            old = (message.message_id, "photo" if (message.photo or message.video or message.animation) else "text")
        if old and old[1] == "text":
            try:
                edited = await message.bot.edit_message_text(
                    chat_id=chat_id, message_id=old[0], text=text, reply_markup=markup
                )
                _set(chat_id, old[0], "text")
                return edited
            except TelegramBadRequest as exc:
                if _same_message_error(exc):
                    return None
                # This message may have been deleted or become uneditable.
        sent = await message.bot.send_message(chat_id, text, reply_markup=markup)
        _set(chat_id, sent.message_id, "text")
        if old and old[0] != sent.message_id:
            await _remove(message.bot, chat_id, old[0])
        return sent


async def render_photo(message, photo, caption, markup=None, filename="screen.jpg"):
    """Keep the single photo screen editable; handle a text-to-media change safely."""
    chat_id = message.chat.id
    async with _lock(chat_id):
        old = _get(chat_id)
        if old is None and getattr(message, "from_user", None) and message.from_user.is_bot:
            old = (message.message_id, "photo" if (message.photo or message.video or message.animation) else "text")
        def media():
            if isinstance(photo, bytes):
                return BufferedInputFile(photo, filename=filename)
            if isinstance(photo, Path):
                return FSInputFile(photo)
            return photo
        if old and old[1] == "photo":
            try:
                edited = await message.bot.edit_message_media(
                    chat_id=chat_id, message_id=old[0],
                    media=InputMediaPhoto(media=media(), caption=caption[:1024]),
                    reply_markup=markup,
                )
                _set(chat_id, old[0], "photo")
                return edited
            except TelegramBadRequest as exc:
                if _same_message_error(exc):
                    return None
        sent = await message.bot.send_photo(
            chat_id, photo=media(), caption=caption[:1024], reply_markup=markup
        )
        _set(chat_id, sent.message_id, "photo")
        if old and old[0] != sent.message_id:
            await _remove(message.bot, chat_id, old[0])
        return sent


async def render_rich(message, markdown, fallback_text, markup=None):
    """Bot API 10.3 rich home. Preserve edit-in-place; gracefully fall back on old clients.

    Only text/rich screens are edited as text. Photo screens are replaced and
    removed. A failed rich method is never allowed to leave two active screens.
    """
    chat_id = message.chat.id
    rich = InputRichMessage(markdown=markdown)
    async with _lock(chat_id):
        old = _get(chat_id)
        if old is None and getattr(message, "from_user", None) and message.from_user.is_bot:
            old = (message.message_id, "photo" if (
                message.photo or message.video or message.animation
            ) else "text")
        if old and old[1] == "text":
            try:
                edited = await message.bot.edit_message_text(
                    chat_id=chat_id, message_id=old[0], rich_message=rich,
                    reply_markup=markup,
                )
                _set(chat_id, old[0], "text")
                return edited
            except (TelegramBadRequest, AttributeError, TypeError) as exc:
                if _same_message_error(exc):
                    return None
                # Rich messages unsupported: try to reuse the same text message.
                try:
                    edited = await message.bot.edit_message_text(
                        chat_id=chat_id, message_id=old[0], text=fallback_text,
                        reply_markup=markup,
                    )
                    _set(chat_id, old[0], "text")
                    return edited
                except TelegramBadRequest as fallback_exc:
                    if _same_message_error(fallback_exc):
                        return None
        try:
            sent = await message.bot.send_rich_message(
                chat_id=chat_id, rich_message=rich, reply_markup=markup,
            )
        except (TelegramBadRequest, AttributeError, TypeError):
            sent = await message.bot.send_message(
                chat_id, fallback_text, reply_markup=markup,
            )
        _set(chat_id, sent.message_id, "text")
        if old and old[0] != sent.message_id:
            await _remove(message.bot, chat_id, old[0])
        return sent
