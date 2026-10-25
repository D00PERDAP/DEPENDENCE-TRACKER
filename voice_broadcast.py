"""Admin text-to-voice announcements for opted-in bot audience.

Uses the standard Russian male Microsoft neural voice. A regional accent is
not guaranteed; colloquial word choice supplies Maga character.
"""
import asyncio
import logging
import os
import sqlite3
import sys
import re
import textwrap
import shutil
import tempfile
from pathlib import Path

from aiogram import F
import ui_manager
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)
MAX_CHARS = 700
VOICE_NAME = "ru-RU-DmitryNeural"
VOICE_RATE = "-8%"
VOICE_PITCH = "-10Hz"


VOICE_DB = Path(__file__).resolve().parent / "data" / "maga_voice_settings.sqlite3"
SPEED_CHOICES = {"slow": ("🐢 Медленно · 0.80x", "0.80"), "relaxed": ("😎 Спокойно · 0.90x", "0.90"), "normal": ("⚡ Обычно · 1.00x", "1.00")}

VOICE_CHOICES = {
    "edge": ("🎙 Дмитрий · обычный", None),
    "gamat": ("🦅 Гамат · Silero", "ru_gamat"),
    "eduard": ("🦅 Эдуард · Silero", "ru_eduard"),
}


def voice_db():
    VOICE_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(VOICE_DB, timeout=15)
    c.execute(
        "CREATE TABLE IF NOT EXISTS profiles "
        "(admin_id INTEGER PRIMARY KEY, voice TEXT NOT NULL)"
    )
    columns = {r[1] for r in c.execute("PRAGMA table_info(profiles)")}
    if "speed" not in columns:
        c.execute("ALTER TABLE profiles ADD COLUMN speed TEXT NOT NULL DEFAULT 'normal'")
    c.commit()
    return c


def get_speed(admin_id):
    with voice_db() as c:
        row = c.execute("SELECT speed FROM profiles WHERE admin_id=?", (admin_id,)).fetchone()
    val = row[0] if row else "normal"
    return val if val in SPEED_CHOICES else "normal"


def set_speed(admin_id, speed):
    if speed not in SPEED_CHOICES:
        raise ValueError("Неизвестная скорость")
    with voice_db() as c:
        c.execute("INSERT INTO profiles(admin_id,voice,speed) VALUES(?,?,?) "
                  "ON CONFLICT(admin_id) DO UPDATE SET speed=excluded.speed",
                  (admin_id, "edge", speed))


def speed_filter(speed):
    if speed not in SPEED_CHOICES:
        raise ValueError("Неизвестная скорость")
    return "atempo=" + SPEED_CHOICES[speed][1]


def get_voice(admin_id):
    with voice_db() as c:
        row = c.execute(
            "SELECT voice FROM profiles WHERE admin_id=?", (admin_id,)
        ).fetchone()
    value = row[0] if row else "edge"
    return value if value in VOICE_CHOICES else "edge"


def set_voice(admin_id, choice):
    if choice not in VOICE_CHOICES:
        raise ValueError("Неизвестный голос Маги")
    with voice_db() as c:
        c.execute(
            "INSERT INTO profiles(admin_id,voice) VALUES(?,?) "
            "ON CONFLICT(admin_id) DO UPDATE SET voice=excluded.voice",
            (admin_id, choice),
        )


async def synthesize_silero(text, output, choice, speed="normal"):
    """Invoke optional local worker; no third-party paid API involved."""
    speaker = VOICE_CHOICES[choice][1]
    if not speaker:
        raise ValueError("Для этого варианта Silero не нужен")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("На сервере не установлен ffmpeg")
    source = output.parent / "silero_text.txt"
    wav = output.parent / "silero.wav"
    source.write_text(text, encoding="utf-8")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).with_name("silero_local_voice.py")),
        str(source), str(wav), "--speaker", speaker,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(
            "Silero не успел озвучить текст за 3 минуты. "
            "Проверь память и загрузку VPS."
        ) from exc
    if proc.returncode or not wav.exists():
        detail = err.decode("utf-8", "replace").splitlines()
        reason = detail[-1][:160] if detail else "Неизвестная ошибка"
        raise RuntimeError(
            "Локальный голос Silero не запустился: " + reason
            + ". Установи torch, torchaudio и omegaconf на VPS."
        )
    conv = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y", "-i", str(wav), "-vn", "-ac", "1",
        "-af", speed_filter(speed),
        "-c:a", "libopus", "-b:a", "32k", str(output),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await conv.communicate()
    if conv.returncode or not output.exists() or not output.stat().st_size:
        raise RuntimeError("Не получилось преобразовать Silero WAV в Telegram OGG.")


class VoiceFlow(StatesGroup):
    text = State()
    confirm = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


def split_voice_text(text, limit=135):
    """Preserve the complete text; break at sentences where possible."""
    if limit < 20:
        raise ValueError("Voice chunk limit must be at least 20")
    sentences = re.split(r"(?<=[.!?…])\s+|\n+", text.strip())
    pieces = []
    for sentence in sentences:
        if not sentence.strip():
            continue
        pieces.extend(textwrap.wrap(
            sentence.strip(), width=limit, break_long_words=True,
            break_on_hyphens=False
        ))
    chunks = pieces
    return chunks


async def synthesize_voice(text, output, speed="normal"):
    """Render short TTS requests and combine as one Telegram OGG/Opus note."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("На сервере не установлен ffmpeg.")
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("Не установлен edge-tts. Обнови зависимости.") from exc

    chunks = split_voice_text(text)
    if not chunks:
        raise RuntimeError("Текст для озвучивания пустой.")

    # File paths are controlled by us (not by message content).
    manifest = output.parent / "maga_segments.txt"
    paths = []
    for index, part in enumerate(chunks, start=1):
        mp3 = output.parent / f"maga_part_{index:02d}.mp3"
        for attempt in range(3):
            try:
                await asyncio.wait_for(
                    edge_tts.Communicate(
                        part, voice=VOICE_NAME, rate=VOICE_RATE,
                        pitch=VOICE_PITCH
                    ).save(str(mp3)),
                    timeout=35,
                )
                if not mp3.exists() or mp3.stat().st_size == 0:
                    raise RuntimeError("Получен пустой аудиофайл.")
                break
            except (Exception, asyncio.TimeoutError) as exc:
                if attempt == 2:
                    raise RuntimeError(
                        f"Не удалось озвучить часть {index} из {len(chunks)} "
                        f"({type(exc).__name__}). Попробуй ещё раз."
                    ) from exc
                logger.warning(
                    "Maga voice chunk %s/%s attempt %s failed: %s",
                    index, len(chunks), attempt + 1, type(exc).__name__
                )
                await asyncio.sleep(1.5 * (attempt + 1))
        paths.append(mp3)

    # Concatenate source files before transcoding, avoiding a separate voice
    # note per sentence. One output means one Telegram preview and one send.
    if len(paths) > 1:
        silence = output.parent / "maga_pause.mp3"
        gap = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", "0.50", "-c:a", "libmp3lame", str(silence),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await gap.communicate()
        if gap.returncode != 0:
            raise RuntimeError("Не получилось вставить паузы в голосовое.")
        playlist = []
        for i, path in enumerate(paths):
            playlist.append(path)
            if i < len(paths) - 1:
                playlist.append(silence)
    else:
        playlist = paths

    manifest.write_text(
        "".join(f"file '{path.name}'\n" for path in playlist),
        encoding="utf-8",
    )
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y", "-f", "concat", "-safe", "1",
        "-i", str(manifest), "-vn", "-ac", "1",
        "-af", speed_filter(speed),
        "-c:a", "libopus", "-b:a", "32k", str(output),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not output.is_file() or not output.stat().st_size:
        logger.warning("Voice transcoding failed: %s", err[:300] if err else proc.returncode)
        raise RuntimeError("ffmpeg не смог собрать голосовое в OGG/Opus.")


def install(dp, *, is_admin, connect, render_text, log):
    @dp.callback_query(F.data == "ac:voice")
    async def voice_menu(q, state):
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        await q.answer()
        await state.set_state(VoiceFlow.text)
        current_voice = get_voice(q.from_user.id)
        current_speed = get_speed(q.from_user.id)
        await render_text(q.message,
            "🎙 МАГА | ГОЛОСОВОЙ САЛАМ\n\n"
            "Пиши текст, брат, — Мага озвучит его мужским голосом.\n"
            "Сначала лично послушаешь, потом решишь, пускать ли по вацокам.\n\n"
            f"До 700 символов. Голос: {VOICE_CHOICES[current_voice][0]}.\n"
            "Silero — бесплатный локальный вариант, характер речи слушай в предпросмотре.",
            keyboard([[("🎭 Выбрать голос", "ac:voicevoices")],
                      [("🐢 Скорость речи", "ac:voicespeeds")],
                      [("⬅️ В штаб", "ac:voicecancel")]]))

    @dp.callback_query(F.data == "ac:voicevoices")
    async def voice_voices(q, state):
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        await q.answer()
        current = get_voice(q.from_user.id)
        buttons = [
            [(("✅ " if current == key else "") + label, f"ac:setvoice:{key}")]
            for key, (label, _) in VOICE_CHOICES.items()
        ]
        buttons.append([("⬅️ Назад к озвучке", "ac:voice")])
        await render_text(
            q.message,
            "🎭 МАГА | ВЫБИРАЙ ГОЛОС, БРАТ\n\n"
            "Дмитрий — прежний стабильный онлайн-голос.\n"
            "Гамат и Эдуард — экспериментальные голоса локальной "
            "бесплатной модели Silero. Акцент не гарантирован.\n"
            "Для Silero потребуется дополнительная установка на VPS.",
            keyboard(buttons),
        )

    @dp.callback_query(F.data.startswith("ac:setvoice:"))
    async def set_voice_option(q, state):
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        choice = q.data.rsplit(":", 1)[-1]
        if choice not in VOICE_CHOICES:
            return await q.answer("Такого голоса нет, брат.", show_alert=True)
        set_voice(q.from_user.id, choice)
        await voice_menu(q, state)

    @dp.callback_query(F.data == "ac:voicespeeds")
    async def voice_speeds(q):
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        await q.answer()
        current = get_speed(q.from_user.id)
        rows = [[(("✅ " if current == key else "") + label, f"ac:setspeed:{key}")]
                for key, (label, _) in SPEED_CHOICES.items()]
        rows.append([("⬅️ К озвучке, брат", "ac:voice")])
        await render_text(q.message,
            "🐢 МАГА | ТЕМП РЕЧИ\n\n"
            "Медленно — тянем слова, жи есть!\n"
            "Спокойно — без суеты.\n"
            "Обычно — как раньше.\n\n"
            "Темп меняется без повышения или понижения голоса.",
            keyboard(rows))

    @dp.callback_query(F.data.startswith("ac:setspeed:"))
    async def set_speed_option(q, state):
        if not is_admin(q.from_user.id):
            return await q.answer("Нет доступа", show_alert=True)
        choice = q.data.rsplit(":", 1)[-1]
        if choice not in SPEED_CHOICES:
            return await q.answer("Такого темпа нет, брат.", show_alert=True)
        set_speed(q.from_user.id, choice)
        await voice_menu(q, state)

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
            await render_text(m, "🎙 Мага записывает салам, брат…", None)
            with tempfile.TemporaryDirectory(prefix="maga_tts_") as work:
                path = Path(work) / "maga.ogg"
                selected_voice = get_voice(m.from_user.id)
                selected_speed = get_speed(m.from_user.id)
                if selected_voice == "edge":
                    await synthesize_voice(body, path, selected_speed)
                else:
                    await synthesize_silero(body, path, selected_voice, selected_speed)
                preview = await ui_manager.render_voice(
                    m, FSInputFile(path),
                    caption="🎙 МАГА | ПРОСЛУШКА\n\nЗацени, вацок. "
                            "Отправлять всем или ещё переделаем?",
                    markup=keyboard([
                        [("📢 Отправить подписчикам", "ac:voicesend")],
                        [("📝 Переделать текст", "ac:voice")],
                        [("❌ Отбой, брат", "ac:voicecancel")],
                    ]),
                )
            await state.update_data(
                maga_voice_file_id=preview.voice.file_id,
                maga_voice_text=body,
                maga_voice_engine=selected_voice,
                maga_voice_speed=selected_speed,
                maga_voice_preview_id=preview.message_id,
            )
            await state.set_state(VoiceFlow.confirm)
        except Exception as exc:
            logger.exception("Voice preview failed")
            message = str(exc)
            if not isinstance(exc, RuntimeError):
                message = ("Не удалось отправить предпросмотр в Telegram "
                           f"({type(exc).__name__}). Проверь журнал.")
            await render_text(m,
                f"❌ Брат, голос не получился.\n\nПричина: {message}\n\n"
                "Напиши текст ещё раз, вацок.", None
            )
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
