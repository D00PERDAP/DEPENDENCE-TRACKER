"""Telegram UX v2: short steps, urgent safety, risk interventions, private charts.

Mounted onto the existing aiogram Dispatcher; legacy callbacks, user data and
existing SQLite sessions remain compatible.
"""
from __future__ import annotations

import asyncio
import io
import os
import random
from datetime import timedelta

from aiogram import F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile

import context_memes
import harm_reduction as safety
import stats_dashboard
import v2_data as db

DEFAULT_SUGGESTIONS = (
    "Не звонить бывшей", "Не садиться за руль",
    "Не тратить лишние деньги", "Не покупать ещё", "Не смешивать вещества",
    "Не писать всем подряд",
)
PROBLEMS = {
    "ex": "бывш",
    "contacts": "друз",
    "money": "деньг",
    "travel": "рул",
    "more": "ещё",
    "sleep": "спать",
}
REMINDER_LINES = [
    "Не отдавай завтрашнему себе проблемы, которые можно не создавать сегодня.",
    "Твоё сообщение может подождать. Скриншот завтра — нет.",
    "Бот не полиция. Ты сам попросил напомнить: не натвори делов.",
    "Худшие сценарии ночи начинаются со слов «да пофиг».",
    "Никому не надо ничего доказывать сегодня. Особенно банку.",
    "Если хочется срочно изменить всю жизнь — сначала проживи эти 10 минут.",
    "Шанс избежать тупого решения всё ещё у тебя в руках.",
    "Не каждый внутренний «гениальный план» достоин реализации.",
    "У тебя уже есть список запретов. Загляни, пока не поздно.",
    "Завтра твоей памяти пригодятся хорошие новости, а не протокол событий.",
    "Молчать иногда выгоднее, чем объясняться всю следующую неделю.",
    "Сессия не обязана превращаться в сериал с плохим финалом.",
    "Сейчас задача не геройствовать. Задача — ничего не усложнить.",
    "Не садись за руль, не трать лишнего и не принимай решения на автомате.",
    "Мозг может требовать немедленного действия. Не обязан ему подчиняться.",
    "Положи телефон. Ничего срочного в четыре утра нет.",
    "Если чувствуешь, что теряешь контроль — напиши трезвому человеку.",
    "Ночной тебе ещё отвечать перед утренним тобой.",
    "Сделать паузу — это тоже действие. И довольно умное.",
    "То, что кажется прекрасной идеей сейчас, может остаться скриншотом навсегда.",
]
RISK_NUDGES = {
    "more": "🛑 Не продолжай на автомате. Отложи действие на 10 минут — этот импульс не командир.",
    "ex": "📵 Не открывай чат с бывшей. Напиши в заметки и ничего не отправляй.",
    "contacts": "📱 Голосовое может подождать. Включи «Не беспокоить».",
    "money": "💸 Закрой банковское приложение. Это не ночь для переводов.",
    "travel": "🚕 За руль нельзя. Если нужно домой, попроси трезвого человека помочь.",
    "sleep": "🌙 Не пытайся уснуть с помощью алкоголя или других веществ.",
    "paranoia": "🫀 Если теряешь ориентацию или чувствуешь угрозу себе, ищи помощь у трезвого человека и врача.",
}


def risk_from_titles(titles):
    hay = " ".join(titles).casefold()
    for tag, term in PROBLEMS.items():
        if term in hay:
            return tag
    if "покуп" in hay or "догон" in hay:
        return "more"
    if "писа" in hay or "звон" in hay:
        return "contacts"
    if "ехать" in hay:
        return "travel"
    return "check"


def reminder_text(substance, titles, event_tag=None):
    risk = event_tag if event_tag in safety.IMPULSE_ADVICE else risk_from_titles(titles)
    main = RISK_NUDGES.get(risk) or random.choice(REMINDER_LINES)
    selected = "\n".join("☑️ " + x[:85] for x in titles[:4])
    return (f"🔔 НЕ НАТВОРИ ДЕЛОВ • {substance}\n\n{main}\n\n"
            f"{selected or 'Не действуй на автомате.'}\n\n"
            "Если что-то тревожит, нажми «Мне плохо» или «Меня несёт»."), risk


def reminder_keyboard(kb):
    return kb([
        [("👌 Всё нормально", "v2:feel:ok"), ("🔥 Меня несёт", "v2:impulses")],
        [("🆘 Мне плохо", "v2:safety"), ("⏸ Тихий режим", "v2:quiet")],
        [("🎭 Мем по ситуации", "v2:meme")],
    ])


async def followup_loop(bot, connect, kb):
    """Persistent, opt-in 10-minute wellbeing/urge follow-ups, not emergency care."""
    while True:
        try:
            for item in db.claim_due_followups(connect):
                text = ("⏳ Десять минут прошло. Как сейчас? Желание действовать сразу стало слабее?"
                        if item["kind"] == "urge" else
                        "🫀 Проверка через 10 минут. Тебе лучше? При опасных симптомах не жди ответа бота — вызывай скорую.")
                try:
                    await bot.send_message(
                        item["user_id"], text,
                        reply_markup=kb([
                            [("✅ Стало лучше", "v2:better"), ("😣 Всё ещё тяжело", "v2:impulses")],
                            [("🆘 Нужна помощь", "v2:safety")],
                        ]),
                    )
                except Exception as exc:
                    print(f"Followup delivery: {type(exc).__name__}")
        except Exception as exc:
            print(f"Followup scheduler: {type(exc).__name__}: {exc}")
        await asyncio.sleep(20)


def install(dp, *, connect, active, picked, ensure_preset, kb, display,
            now, stamp, meme_key, record_meme, meme_buttons):
    """Register handlers without importing bot.py again when launched as __main__."""

    def setup_state(c, uid):
        ensure_preset(c, uid)
        settings = db.get_settings(c, uid)
        current = active(c, uid)
        return settings, current

    async def panel(q, title, rows):
        await display(q.message, title, kb(rows))

    async def choose_safety(q, manual=False):
        with connect() as c:
            settings = db.get_settings(c, q.from_user.id)
        rows = [[(name, f"v2:med:{i}")] for i, name in enumerate(safety.SUBSTANCES)]
        rows.append([("⬅️ Назад", "v2:home")])
        await panel(q, "🆘 ПОМОЩЬ ПО ВЕЩЕСТВУ\n\n"
                    "Что принимал(а)? Если не знаешь — последний пункт.\n"
                    "Если проблемы с дыханием, сильная боль в груди, потеря сознания, "
                    "судороги или перегрев — не выбирай меню, вызывай скорую!",
                    [[("🚨 Нужна скорая прямо сейчас", "v2:emergency")]] + rows)

    async def show_symptoms(q):
        with connect() as c:
            substance = db.get_settings(c, q.from_user.id)["last_substance"]
        rows = [[(label, f"v2:sym:{tag}")] for tag, label in safety.SYMPTOMS]
        rows += [[("🔥 Хочу продолжить / натворить делов", "v2:impulses")],
                 [("🚨 Нужна скорая", "v2:emergency")],
                 [("⬅️ Другое вещество", "v2:safety")]]
        await panel(q, f"🩺 {substance}\n\nЧто происходит сейчас?\n"
                    "Симптомы важнее мемов и напоминаний.", rows)

    async def send_chart(message, uid):
        with connect() as c:
            data = db.monthly_summary(c, uid)
        try:
            image = await asyncio.to_thread(stats_dashboard.make_dashboard, data)
            await message.answer_photo(
                BufferedInputFile(image, filename="my-statistics.jpg"),
                caption=stats_dashboard.summary_caption(data),
                reply_markup=kb([
                    [("🔄 Обновить", "v2:stats"), ("☑️ Мои запреты", "check")],
                    [("🏠 Меню", "v2:home"), ("🗑 Мои данные", "delete_ask")],
                ]),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"Stats chart rendering problem: {type(exc).__name__}: {exc}")
            await message.answer(stats_dashboard.summary_caption(data) +
                                 "\n\nНе удалось построить изображение. Проверь шрифты и Pillow на сервере.")

    @dp.message(F.text, Command("addmeme"))
    async def add_meme_help(message):
        admins = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
        if not message.from_user or message.from_user.id not in admins:
            return
        await message.answer("🎭 ДОБАВИТЬ СВОЙ МЕМ\n\n"
                             "Отправь фото с подписью, например:\n"
                             "/addmeme meph ex YES\n\n"
                             "meph — вещество, ex — ситуация, YES — ты подтверждаешь, "
                             "что имеешь право распространять изображение.\n"
                             "Допустимые темы: meph, alpha, stimulant, mdma, weed, alcohol, "
                             "ketamine, opioid, sedative, psychedelic, general.\n"
                             "Ситуации: more, ex, contacts, money, travel, paranoia, "
                             "sleep, check, general, after, ok.")

    @dp.message(F.photo, F.caption.startswith("/addmeme"))
    async def add_meme_photo(message):
        admins = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
        if not message.from_user or message.from_user.id not in admins:
            return
        parts = (message.caption or "").split()
        if len(parts) != 4 or parts[0] != "/addmeme" or parts[3] != "YES":
            return await message.answer(
                "Формат подписи: /addmeme meph ex YES\n"
                "YES означает, что ты проверил мем и имеешь право его распространять.")
        photo = message.photo[-1]
        if photo.file_size and photo.file_size > 8_000_000:
            return await message.answer("Фото слишком большое (максимум 8 МБ).")
        buffer = io.BytesIO()
        try:
            await message.bot.download(photo.file_id, destination=buffer)
            target = await asyncio.to_thread(
                context_memes.import_admin_photo,
                buffer.getvalue(), parts[1], parts[2], message.from_user.id,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            return await message.answer(f"Не получилось добавить: {type(exc).__name__}. Проверь теги и фото.")
        await message.answer(
            f"✅ Мем добавлен: {parts[1]} / {parts[2]}.\n"
            "Теперь при таком сценарии он может прийти пользователю.",
            reply_markup=kb([[("🎭 Проверить", "v2:meme")]]),
        )

    @dp.message(Command("stats"))
    async def stats_command(message):
        await send_chart(message, message.from_user.id)

    @dp.message(Command("helpme"))
    async def emergency_command(message):
        await message.answer(safety.EMERGENCY_TEXT,
                             reply_markup=kb([[("🆘 Выбрать симптомы", "v2:safety")]]))

    @dp.callback_query(F.data.startswith("v2:"))
    async def v2(q):
        uid = q.from_user.id
        data = q.data.split(":")
        action = data[1] if len(data) > 1 else ""
        param = data[2] if len(data) > 2 else ""
        await q.answer()

        if action == "home":
            with connect() as c:
                settings, session = setup_state(c, uid)
                count = len(picked(c, uid))
            text = ("💀 НЕ НАТВОРИ ДЕЛОВ\n\n"
                    f"{'🔴 Сопровождение активно' if session else '⚪ Сессия не начата'}\n"
                    f"Твоих запретов: {count}\n"
                    f"Напоминания: {'⏸ тишина' if settings['quiet'] else str(settings['base_interval']) + ' минут'}\n\n"
                    "Что нужно прямо сейчас?")
            await panel(q, text, [
                [("▶️ Начать / настроить", "v2:start"), ("☑️ Мои запреты", "check")],
                [("🫀 Как я?", "v2:checkin"), ("🆘 Мне плохо", "v2:safety")],
                [("🔥 Останови меня", "v2:impulses")],
                [("📊 Моя статистика", "v2:stats"), ("🎭 Мем", "v2:meme")],
                [("⚙️ Напоминания", "v2:settings")],
                *([[("⏹ Завершить", "v2:finish")]] if session else []),
            ])
            return

        if action == "start":
            with connect() as c:
                settings, session = setup_state(c, uid)
                if not picked(c, uid):
                    c.execute("UPDATE checklist SET selected=1 WHERE user_id=? AND title IN (?,?)",
                              (uid, "Не садиться за руль", "Не смешивать вещества"))
            if session:
                return await panel(q, "🔴 У тебя уже включено сопровождение. "
                                   "Можно изменить частоту или завершить.", [
                    [("🔔 Частота", "v2:settings"), ("☑️ Запреты", "check")],
                    [("⏹ Завершить", "v2:finish"), ("🏠 Меню", "v2:home")]])
            rows = [[(name, f"v2:sub:{i}")] for i, name in enumerate(safety.SUBSTANCES)]
            rows += [[("☑️ Сначала выбрать запреты", "check")], [("🏠 Меню", "v2:home")]]
            return await panel(q, "▶️ НАЧАЛО\n\nОтметь вещество или «Другое / не знаю». "
                               "Можно выбрать без объяснений. Затем назначим частоту напоминаний.", rows)

        if action == "sub":
            try:
                substance = safety.SUBSTANCES[int(param)]
                if int(param) < 0:
                    raise IndexError
            except (ValueError, IndexError):
                return await panel(q, "Неизвестный пункт.", [[("⬅️ Назад", "v2:start")]])
            with connect() as c:
                db.set_setting(c, uid, last_substance=substance)
            return await panel(q, f"✅ {substance}\n\n"
                               "Как часто присылать напоминания? Начнём сразу после выбора.", [
                [("⚡ Каждые 10 мин", "v2:freq:10"), ("🔔 15 мин", "v2:freq:15")],
                [("🕒 30 мин", "v2:freq:30"), ("🌙 60 мин", "v2:freq:60")],
                [("☑️ Мои запреты", "check"), ("⬅️ Назад", "v2:start")]])

        if action in ("freq", "setfreq"):
            try:
                minutes = int(param)
                if minutes not in (10, 15, 30, 60):
                    raise ValueError
            except ValueError:
                return await panel(q, "Неверная частота.", [[("⬅️ Назад", "v2:settings")]])
            with connect() as c:
                settings, session = setup_state(c, uid)
                if action == "setfreq" and not session:
                    return await panel(q, "Сначала включи сопровождение.", [
                        [("▶️ Начать", "v2:start"), ("🏠 Меню", "v2:home"),
                    ]])
                created = session is None
                if created:
                    cur = c.execute("INSERT INTO sessions(user_id,substance,started) VALUES(?,?,?)",
                                    (uid, settings["last_substance"], stamp()))
                    sid = cur.lastrowid
                    c.execute("INSERT INTO users(user_id,substance) VALUES(?,?) "
                              "ON CONFLICT(user_id) DO UPDATE SET substance=excluded.substance",
                              (uid, settings["last_substance"]))
                else:
                    sid = session["id"]
                db.set_setting(c, uid, base_interval=minutes, quiet=0, boost_until=None)
                due = stamp(now() + timedelta(minutes=minutes))
                c.execute("""INSERT INTO alerts(user_id,session_id,interval_min,next_at)
                             VALUES(?,?,?,?) ON CONFLICT(user_id)
                             DO UPDATE SET session_id=excluded.session_id,interval_min=excluded.interval_min,
                                           next_at=excluded.next_at""", (uid, sid, minutes, due))
                selected = [x["title"] for x in picked(c, uid)]
                selected_sub = settings["last_substance"]
            await panel(q,
                f"🔴 {'СОПРОВОЖДЕНИЕ ВКЛЮЧЕНО' if created else 'ЧАСТОТА ИЗМЕНЕНА'}\n\n"
                f"Вещество: {selected_sub}\n"
                f"Напоминания: каждые {minutes} минут.\n"
                f"Запретов выбрано: {len(selected)}\n\n"
                "Не стоит откладывать помощь, если тебе физически плохо.", [
                    [("🔥 Меня несёт", "v2:impulses"), ("🆘 Мне плохо", "v2:safety")],
                    [("☑️ Изменить запреты", "check"), ("🏠 Меню", "v2:home")],
                ])
            if created:
                await context_memes.send(q.message, uid, selected_sub,
                                         risk_from_titles(selected), connect,
                                         meme_key, record_meme, meme_buttons)
            return

        if action == "impulses":
            return await panel(q, "🔥 ОСТАНОВИ МЕНЯ\n\nЧто сейчас больше всего тянет сделать?\n"
                               "Нажми один вариант — без длинных анкет.", [
                *[[(label, f"v2:hold:{tag}")] for tag, label in safety.IMPULSES],
                [("🆘 Физически плохо", "v2:safety"), ("🏠 Меню", "v2:home")],
            ])

        if action == "hold":
            if param not in safety.IMPULSE_ADVICE:
                return await panel(q, "Неизвестная проблема.", [[("🏠 Меню", "v2:home")]])
            with connect() as c:
                settings, session = setup_state(c, uid)
                substance = session["substance"] if session else settings["last_substance"]
                db.record_event(c, uid, session["id"] if session else None, "risk", param)
            await panel(q, safety.impulse_message(param, substance), [
                [("⏳ Проверить через 10 мин", "v2:wait:urge"), ("✅ Передумал", "v2:better")],
                [("🔔 Чаще напоминать", "v2:boost"), ("🆘 Мне плохо", "v2:safety")],
                [("🏠 Меню", "v2:home")],
            ])
            if param != "paranoia":
                await context_memes.send(q.message, uid, substance, param, connect,
                                         meme_key, record_meme, meme_buttons)
            return

        if action == "feel":
            if param == "panic":
                with connect() as c:
                    settings, session = setup_state(c, uid)
                    substance = session["substance"] if session else settings["last_substance"]
                    db.record_event(c, uid, session["id"] if session else None, "state", "panic")
                info = safety.get_card(substance, "panic")
                return await panel(q, f"{info.title}\n\n{info.text}", [
                    [("🕒 Проверить через 10 мин", "v2:wait:wellbeing")],
                    [("🚨 Серьёзные симптомы", "v2:emergency"), ("🩺 Другой симптом", "v2:questions")],
                ])
            if param == "ok":
                with connect() as c:
                    settings, session = setup_state(c, uid)
                    db.record_event(c, uid, session["id"] if session else None, "state", "ok")
                return await panel(q, "👌 Отметил: всё нормально.\n\n"
                                   "Не нужно ничего доказывать себе или другим сегодня.", [
                    [("🎭 Мем", "v2:meme"), ("🔥 Меня несёт", "v2:impulses")],
                    [("🆘 Мне плохо", "v2:safety"), ("🏠 Меню", "v2:home")],
                ])
            return await panel(q, "Отметь состояние:", [
                [("🔥 Меня несёт", "v2:impulses"), ("🆘 Мне плохо", "v2:safety")],
            ])

        if action == "better":
            with connect() as c:
                settings, session = setup_state(c, uid)
                was_risk = db.resolve_recent(c, uid)
                db.record_event(c, uid, session["id"] if session else None, "state", "better")
            return await panel(q, "✅ Отлично, что стало спокойнее.\n"
                               + ("Ты отметил, что последний импульс удалось переждать." if was_risk else
                                  "Отметка сохранена.") +
                               "\nЕсли снова станет плохо — помощь доступна из меню.", [
                [("🔥 Ещё нужна поддержка", "v2:impulses"), ("🏠 Меню", "v2:home")],
            ])

        if action == "boost":
            with connect() as c:
                settings, session = setup_state(c, uid)
                if not session:
                    return await panel(q, "Сначала начни сопровождение.", [[("▶️ Начать", "v2:start")]])
                until = stamp(now() + timedelta(hours=1))
                db.set_setting(c, uid, boost_until=until, quiet=0)
                c.execute("UPDATE alerts SET next_at=? WHERE user_id=? AND session_id=?",
                          (stamp(now() + timedelta(minutes=10)), uid, session["id"]))
            return await panel(q, "⚡ На ближайший час включены напоминания примерно каждые 10 минут.\n"
                               "Выключить их можно в «Настройках».",
                               [[("⚙️ Настроить", "v2:settings"), ("🏠 Меню", "v2:home")]])

        if action == "wait":
            kind = param if param in ("urge", "wellbeing") else "urge"
            with connect() as c:
                db.schedule_followup(c, uid, kind, 10, now())
            return await panel(q,
                "⏳ Напишу через 10 минут, чтобы проверить состояние.\n"
                "Если становится физически хуже — не жди уведомления, обращайся за помощью.", [
                    [("✅ Передумал", "v2:better"), ("🆘 Мне плохо", "v2:safety")],
                    [("🏠 Меню", "v2:home")],
                ])

        if action == "checkin":
            return await panel(q, "🫀 КАК ТЫ?\n\nВыбирай, что ближе прямо сейчас. "
                               "Это не диагноз — ответы помогут подобрать действие.", [
                [("👌 В порядке", "v2:feel:ok"), ("😰 Паника", "v2:feel:panic")],
                [("🔥 Тянет творить трэш", "v2:impulses"), ("😵 Не сплю", "v2:hold:sleep")],
                [("🤢 Физически плохо", "v2:safety"), ("🚨 Срочно", "v2:emergency")],
                [("🏠 Меню", "v2:home")],
            ])

        if action == "safety":
            with connect() as c:
                settings, session = setup_state(c, uid)
                current_sub = session["substance"] if session else settings["last_substance"]
                if current_sub not in safety.SUBSTANCES:
                    current_sub = "Другое / не знаю"
                if session and settings["last_substance"] == "Другое / не знаю":
                    db.set_setting(c, uid, last_substance=current_sub)
            return await panel(q, f"🆘 МНЕ ПЛОХО\n\nПоследнее вещество: {current_sub}\n"
                               "При боли в груди, затруднении дыхания, судорогах, "
                               "потере сознания, перегреве — сразу вызывай скорую.", [
                [("🚨 Немедленная помощь", "v2:emergency")],
                [("🫀 Выбрать симптом", "v2:questions")],
                [("💊 Другое вещество", "v2:meds")],
                [("🏠 Меню", "v2:home")],
            ])

        if action in ("faq", "meds"):
            return await choose_safety(q)

        if action == "med":
            try:
                substance = safety.SUBSTANCES[int(param)]
                if int(param) < 0:
                    raise IndexError
            except (ValueError, IndexError):
                return await panel(q, "Неизвестное вещество.", [[("⬅️ Назад", "v2:safety")]])
            with connect() as c:
                db.set_setting(c, uid, last_substance=substance)
            return await show_symptoms(q)

        if action == "sym":
            with connect() as c:
                settings, session = setup_state(c, uid)
                substance = settings["last_substance"]
                response = safety.get_card(substance, param)
                db.record_event(c, uid, session["id"] if session else None, "state", "bad")
            if response.urgent:
                return await panel(q, response.text, [
                    [("🚨 Как вызвать помощь", "v2:emergency")],
                    [("⬅️ Другие симптомы", "v2:questions")],
                ])
            return await panel(q, f"{response.title}\n\n{response.text}", [
                [("🕒 Проверить через 10 мин", "v2:wait:wellbeing")],
                [("🚨 Стало опасно", "v2:emergency")],
                [("⬅️ Другие симптомы", "v2:questions"), ("🏠 Меню", "v2:home")],
            ])

        if action == "questions":
            return await show_symptoms(q)

        if action == "emergency":
            return await panel(q, safety.EMERGENCY_TEXT, [
                [("🆘 Другие симптомы", "v2:questions")], [("🏠 Меню", "v2:home")],
            ])

        if action == "meme":
            with connect() as c:
                settings, session = setup_state(c, uid)
                substance = session["substance"] if session else settings["last_substance"]
                selected = [x["title"] for x in picked(c, uid)]
                risk_row = c.execute(
                    "SELECT tag FROM v2_events WHERE user_id=? AND event_type='risk' "
                    "ORDER BY id DESC LIMIT 1", (uid,)
                ).fetchone()
                risk = risk_row["tag"] if risk_row else risk_from_titles(selected)
            return await context_memes.send(q.message, uid, substance, risk, connect,
                                            meme_key, record_meme, meme_buttons)

        if action == "stats":
            return await send_chart(q.message, uid)

        if action == "settings":
            with connect() as c:
                settings, session = setup_state(c, uid)
            status = ("⏸ Тихо" if settings["quiet"] else f"🔔 {settings['base_interval']} минут")
            rows = [
                [("10 минут", "v2:setfreq:10"), ("15 минут", "v2:setfreq:15")],
                [("30 минут", "v2:setfreq:30"), ("60 минут", "v2:setfreq:60")],
                [("🔔 Включить" if settings["quiet"] else "⏸ Выключить",
                  "v2:quiet")],
                [("⚡ Усилить на час", "v2:boost"), ("🏠 Меню", "v2:home")],
            ]
            return await panel(q, f"⚙️ НАПОМИНАНИЯ\n\nСейчас: {status}\n"
                               "Выбираешь сам. Важные симптомы требуют помощи, а не спама.", rows)

        if action == "quiet":
            with connect() as c:
                settings, session = setup_state(c, uid)
                new_value = 0 if settings["quiet"] else 1
                db.set_setting(c, uid, quiet=new_value, boost_until=None)
                if not new_value and session:
                    minutes = int(settings["base_interval"])
                    c.execute("UPDATE alerts SET next_at=? WHERE user_id=?",
                              (stamp(now() + timedelta(minutes=minutes)), uid))
            return await panel(q, "⏸ Напоминания выключены." if new_value else
                               "🔔 Напоминания снова включены.", [
                [("⚙️ Настройки", "v2:settings"), ("🏠 Меню", "v2:home")],
            ])

        if action == "finish":
            with connect() as c:
                settings, session = setup_state(c, uid)
                if session:
                    c.execute("UPDATE sessions SET ended=? WHERE id=? AND ended IS NULL",
                              (stamp(), session["id"]))
                c.execute("DELETE FROM alerts WHERE user_id=?", (uid,))
                db.set_setting(c, uid, boost_until=None)
                c.execute("DELETE FROM v2_followups WHERE user_id=?", (uid,))
            return await panel(q, "⏹ Сопровождение завершено.\n\n"
                               "Как всё прошло? Можно просто выбрать вариант.", [
                [("✅ Обошлось", "v2:post:good"), ("😕 Было тяжело", "v2:post:hard")],
                [("📊 Отчёт", "v2:stats"), ("🏠 Меню", "v2:home")],
            ])

        if action == "post":
            tag = "better" if param == "good" else "bad"
            with connect() as c:
                db.record_event(c, uid, None, "state", tag)
            return await panel(q, "Отметка сохранена. Спасибо, что проверил своё состояние.\n"
                               "Сделай паузу и не принимай важных решений на автомате.", [
                [("📊 Моя статистика", "v2:stats"), ("🏠 Меню", "v2:home")],
            ])

        return await panel(q, "Этот пункт недоступен.", [[("🏠 Меню", "v2:home")]])
