"""Admin text-to-voice announcements for opted-in bot audience.

Uses the standard Russian male Microsoft neural voice. A regional accent is
not guaranteed; colloquial word choice supplies Maga character.
"""
import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from aiogram import F
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)
MAX_CHARS = 700
VOICE_NAME = "ru-RU-DmitryNeural"
VOICE_RATE = "-8%"
VOICE_PITCH = "-10Hz"


class VoiceFlow(StatesGroup):
    text = State()
    confirm = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


async def synthesize_voice(text, output):
    """Generate a Telegram voice note (OGG/Opus)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("На сервере не установлен ffmpeg.")
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("Не установлен edge-tts. Обнови зависимости.") from exc

    mp3 = output.with_suffix(".mp3")
    await edge_tts.Communicate(
        text, voice=VOICE_NAME, rate=VOICE_RATE, pitch=VOICE_PITCH
    ).save(str(mp3))
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y", "-i", str(mp3), "-vn", "-ac", "1",
        "-c:a", "libopus", "-b:a", "32k", str(output),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not output.is_file() or not output.stat().st_size:
        logger.warning("Voice transcoding failed: %s", err[:300] if err else proc.returncode)
        raise RuntimeError("Не удалось сделать голосовое из текста.")


def install(dp, *, is_admin, connect, render_text, log):
    @dp.callback_query(F.data == "ac:voice")
    async def voice_menu(q, state):
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        await q.answer()
        await state.set_state(VoiceFlow.text)
        await render_text(q.message,
            "🎙 МАГА | ГОЛОСОВОЙ САЛАМ\n\n"
            "Пиши текст, брат, — Мага озвучит его мужским голосом.\n"
            "Сначала лично послушаешь, потом решишь, пускать ли по вацокам.\n\n"
            "До 700 символов. Для отмены — /cancelvoice.\n"
            "Акцент зависит от движка: естественный кавказский не гарантируется.",
            keyboard([[("⬅️ В штаб", "ac:voicecancel")]]))

    @dp.message(VoiceFlow.text)
    async def receive_text(m, state):
        if not is_admin(m.from_user.id):
            await state.clear()
            return
        body = (m.text or "").strip()
        if body == "/cancelvoice":
            await state.clear()
            return await m.answer("Отбой, брат. Голосовое отменено.")
        if not 1 <= len(body) <= MAX_CHARS:
            return await m.answer("Э, вацок, текст нужен от 1 до 700 символов.")
        try:
            await m.delete()
        except Exception:
            pass
        try:
            await m.answer("🎙 Мага записывает салам, брат…")
            with tempfile.TemporaryDirectory(prefix="maga_tts_") as work:
                path = Path(work) / "maga.ogg"
                await synthesize_voice(body, path)
                preview = await m.bot.send_voice(
                    m.chat.id, FSInputFile(path),
                    caption="🎙 МАГА | ПРОСЛУШКА\n\nЗацени, вацок. "
                            "Отправлять всем или ещё переделаем?",
                    reply_markup=keyboard([
                        [("📢 Отправить подписчикам", "ac:voicesend")],
                        [("📝 Переделать текст", "ac:voice")],
                        [("❌ Отбой, брат", "ac:voicecancel")],
                    ]),
                )
            await state.update_data(
                maga_voice_file_id=preview.voice.file_id,
                maga_voice_text=body,
                maga_voice_preview_id=preview.message_id,
            )
            await state.set_state(VoiceFlow.confirm)
        except Exception as exc:
            logger.warning("Voice preview failure %s: %s", type(exc).__name__, exc)
            await m.answer("❌ Брат, голос не получился. "
                           "Проверь edge-tts, ffmpeg и доступ сервера к сервису речи. "
                           "Напиши текст ещё раз.")
            await state.set_state(VoiceFlow.text)

    @dp.callback_query(F.data == "ac:voicesend")
    async def send(q, state):
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        data = await state.get_data()
        if await state.get_state() != VoiceFlow.confirm.state:
            return await q.answer("Сперва сделай предпросмотр, вацок.", show_alert=True)
        file_id = data.get("maga_voice_file_id")
        preview_id = data.get("maga_voice_preview_id")
        if not file_id or preview_id != q.message.message_id:
            return await q.answer("Предпросмотр устарел, брат.", show_alert=True)
        # Lock this admin's send immediately to avoid double sends.
        await state.clear()
        await q.answer("📢 Отправляем салам вацокам!")
        try:
            await q.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        with connect() as c:
            # Registered users are the same audience as the existing admin broadcast.
            ids = [row[0] for row in c.execute("SELECT user_id FROM bot_user_profiles")]
        sent = failed = 0
        for uid in ids:
            try:
                await q.bot.send_voice(
                    uid, file_id, caption="🦅 Голосовой салам от Маги. Жи есть!"
                )
                sent += 1
            except Exception as exc:
                logger.debug("Voice delivery failed for user %s: %s", uid, type(exc).__name__)
                failed += 1
            await asyncio.sleep(0.08)
        log(q.from_user.id, "voice_broadcast", f"sent={sent}, failed={failed}")
        await render_text(
            q.message,
            f"🦅 МАГА | САЛАМ ДОШЁЛ\n\n"
            f"✅ Доставлено: {sent}\n❌ Не дошло: {failed}\n\n"
            "Жи есть, брат! Мага на связи.",
            keyboard([[("🛠 В штаб", "admin:panel")]]),
        )

    @dp.callback_query(F.data == "ac:voicecancel")
    async def cancel(q, state):
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        await q.answer("Отбой, брат.")
        await state.clear()
        await render_text(q.message, "🎙 Запись отменена, вацок.",
                          keyboard([[("🛠 В штаб", "admin:panel")]]))
