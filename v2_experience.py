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
import meme_engine
import real_memes
import reminder_messages
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


def risks_from_titles(titles):
    """Rotate multiple self-selected limits instead of repeating one risk all day."""
    hay = " ".join(titles).casefold()
    found = [tag for tag, term in PROBLEMS.items() if term in hay]
    if ("покуп" in hay or "догон" in hay) and "more" not in found:
        found.append("more")
    if ("писа" in hay or "звон" in hay) and "contacts" not in found:
        found.append("contacts")
    if "ехать" in hay and "travel" not in found:
        found.append("travel")
    return found or ["check"]


def reminder_text(substance, titles, event_tag=None):
    risk = event_tag if event_tag in safety.IMPULSE_ADVICE else random.choice(risks_from_titles(titles))
    main = RISK_NUDGES.get(risk) or random.choice(REMINDER_LINES)
    selected = "\n".join("☑️ " + x[:85] for x in titles[:4])
    return (f"🔔 Э, БРАТ, НЕ НАТВОРИ ДЕЛОВ • {substance}\n\n{main}\n\n"
            f"{selected or 'Не действуй на автомате.'}\n\n"
            "Если что-то тревожит, нажми «Мне плохо» или «Меня несёт»."), risk


def reminder_keyboard(kb):
    return kb([
        [("👌 Всё нормально", "v2:feel:ok"), ("🔥 Меня несёт", "v2:impulses")],
        [("🆘 Мне плохо", "v2:safety"), ("⏸ Тихий режим", "v2:quiet")],
        [("😂 Прикол по ситуации", "v2:meme")],
    ])


async def followup_loop(bot, connect, kb):
    """Persistent, opt-in 10-minute wellbeing/urge follow-ups, not emergency care."""
    while True:
        try:
            for item in db.claim_due_followups(connect):
                text = (
                    "☀️ Салам, брат! Как ты после сопровождения? Удалось отдохнуть? Не натворил лишнего?\n"
                    "Отметь самочувствие, вацок — это увидишь только в своих раскладах."
                    if item["kind"] == "after" else
                    "⏳ Э, брат, десять минут прошло. Как ты? Порыв уже отпускает?"
                    if item["kind"] == "urge" else
                    "🫀 Вацок, проверка через десять минут. Тебе лучше? При опасных симптомах не жди ответа бота — вызывай скорую."
                )
                try:
                    markup = ([
                        [("✅ Обошлось", "v2:post:good"), ("😕 Было тяжело", "v2:post:hard")],
                        [("📊 Расклад", "v2:stats"), ("🆘 Мне плохо", "v2:safety")],
                    ] if item["kind"] == "after" else [
                        [("✅ Стало лучше", "v2:better"), ("😣 Всё ещё тяжело", "v2:impulses")],
                        [("🆘 Нужна помощь", "v2:safety")],
                    ])
                    uid = item["user_id"]
                    await reminder_messages.replace_existing(bot, uid)
                    sent = await bot.send_message(uid, text, reply_markup=kb(markup))
                    reminder_messages.remember(uid, sent.message_id)
                except Exception as exc:
                    print(f"Followup delivery: {type(exc).__name__}")
        except Exception as exc:
            print(f"Followup scheduler: {type(exc).__name__}: {exc}")
        await asyncio.sleep(20)


def install(dp, *, connect, active, picked, ensure_preset, kb, display,
            now, stamp, meme_key, record_meme, meme_buttons, is_admin=lambda uid: False,
            home_screen=None, show_photo=None):
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
        rows.append([("⬅️ Обратно, брат", "v2:home")])
        await panel(q, "🆘 ПОМОЩЬ ПО ВЕЩЕСТВУ\n\n"
                    "Что принимал(а)? Если не знаешь — последний пункт.\n"
                    "Если проблемы с дыханием, сильная боль в груди, потеря сознания, "
                    "судороги или перегрев — не выбирай меню, вызывай скорую!",
                    [[("🚨 Нужна скорая прямо сейчас", "v2:emergency")]] + rows)

    async def show_symptoms(q):
        with connect() as c:
            substance = db.get_settings(c, q.from_user.id)["last_substance"]
        rows = [[(label, f"v2:sym:{tag}")] for tag, label in safety.SYMPTOMS]
        rows += [[("🔥 Тянет на лишние дела", "v2:impulses")],
                 [("🚨 Нужна скорая", "v2:emergency")],
                 [("⬅️ Другое вещество", "v2:safety")]]
        await panel(q, f"🩺 {substance}\n\nЧто происходит сейчас?\n"
                    "Симптомы важнее мемов и напоминаний.", rows)

    async def send_chart(message, uid):
        with connect() as c:
            data = db.monthly_summary(c, uid)
        try:
            image = await asyncio.to_thread(stats_dashboard.make_dashboard, data)
            markup = kb([
                [("🔄 Обновить", "v2:stats"), ("🏠 На базу", "v2:home")],
            ])
            if show_photo is not None:
                await show_photo(
                    message, image, stats_dashboard.summary_caption(data),
                    markup, filename="maga-stats.jpg",
                )
            else:
                await message.answer_photo(
                    BufferedInputFile(image, filename="maga-stats.jpg"),
                    caption=stats_dashboard.summary_caption(data),
                    reply_markup=markup,
                )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"Stats chart rendering problem: {type(exc).__name__}: {exc}")
            await display(message, stats_dashboard.summary_caption(data) +
                          "\n\nИзображение недоступно.", kb([
                              [("🏠 На базу", "v2:home")],
                          ]))

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
            # One home renderer for /start and all return buttons.
            if home_screen is not None:
                return await home_screen(q.message, uid)
            return await panel(q, "🦅 Салам алейкум, брат!", [
                [("💀 Сопровождение", "v2:start"), ("🆘 Помощь", "v2:safety")],
            ])

        if action == "start":
            with connect() as c:
                settings, session = setup_state(c, uid)
                if not picked(c, uid):
                    c.execute("UPDATE checklist SET selected=1 WHERE user_id=? AND title IN (?,?)",
                              (uid, "Не садиться за руль", "Не смешивать вещества"))
            if session:
                return await panel(q, "🔴 У тебя уже включено сопровождение. "
                                   "Можно изменить частоту или завершить.", [
                    [("⏰ Частота, брат", "v2:settings"), ("☑️ Моё слово", "check")],
                    [("⏹ Закончить, брат", "v2:finish"), ("🏠 На базу", "v2:home")]])
            rows = [[(name, f"v2:sub:{i}")] for i, name in enumerate(safety.SUBSTANCES)]
            rows += [[("☑️ Сначала дай себе слово", "check")], [("🏠 На базу", "v2:home")]]
            return await panel(q, "▶️ НАЧАЛО\n\nОтметь вещество или «Другое / не знаю». "
                               "Можно выбрать без объяснений. Затем назначим частоту напоминаний.", rows)

        if action == "sub":
            try:
                substance = safety.SUBSTANCES[int(param)]
                if int(param) < 0:
                    raise IndexError
            except (ValueError, IndexError):
                return await panel(q, "Э, брат, такой кнопки нет.", [[("⬅️ Обратно, брат", "v2:start")]])
            with connect() as c:
                db.set_setting(c, uid, last_substance=substance)
            return await panel(q, f"✅ {substance}\n\n"
                               "Как часто присылать напоминания? Начнём сразу после выбора.", [
                [("⚡ Каждые 10 минут", "v2:freq:10"), ("🔔 Каждые 15 минут", "v2:freq:15")],
                [("🕒 Каждые 30 минут", "v2:freq:30"), ("🌙 Раз в час", "v2:freq:60")],
                [("☑️ Моё слово", "check"), ("⬅️ Обратно, брат", "v2:start")]])

        if action in ("freq", "setfreq"):
            try:
                minutes = int(param)
                if minutes not in (10, 15, 30, 60):
                    raise ValueError
            except ValueError:
                return await panel(q, "Э, вацок, выбери частоту кнопкой.", [[("⬅️ Обратно, брат", "v2:settings")]])
            with connect() as c:
                settings, session = setup_state(c, uid)
                if action == "setfreq" and not session:
                    return await panel(q, "Сначала включи сопровождение.", [
                        [("▶️ Погнали, вацок", "v2:start"), ("🏠 На базу", "v2:home"),
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
                db.schedule_alert(c, uid, sid, minutes, now())
                selected = [x["title"] for x in picked(c, uid)]
                selected_sub = settings["last_substance"]
            await panel(q,
                f"🔴 {'СОПРОВОЖДЕНИЕ ВКЛЮЧЕНО' if created else 'ЧАСТОТА ИЗМЕНЕНА'}\n\n"
                f"Вещество: {selected_sub}\n"
                f"Напоминания: каждые {minutes} минут.\n"
                f"Запретов выбрано: {len(selected)}\n\n"
                "Не стоит откладывать помощь, если тебе физически плохо.", [
                    [("🔥 Меня несёт", "v2:impulses"), ("🆘 Мне плохо", "v2:safety")],
                    [("☑️ Изменить запреты", "check"), ("🏠 На базу", "v2:home")],
                ])
            # Starting accompaniment must keep the confirmation visible.
            # Memes are requested separately by the user, never auto-replace it.
            return

        if action == "activate10":
            with connect() as c:
                settings, session = setup_state(c, uid)
                if not session:
                    return await panel(q,
                        "Нет активной сессии. Выбери вещество, затем включи 10 минут.",
                        [[("▶️ Погнали, вацок", "v2:start"), ("🏠 На базу", "v2:home")]])
                db.set_setting(c, uid, base_interval=10, quiet=0, boost_until=None)
                db.schedule_alert(c, uid, session["id"], 10, now())
                substance = session["substance"]
                titles = [x["title"] for x in picked(c, uid)]
            await panel(q, "🔔 РАССЫЛКА ВКЛЮЧЕНА\n\n"
                         "Мем-картинка и напоминание каждые 10 минут.\n"
                         "Первое тестовое фото отправляю прямо сейчас.\n"
                         "При необходимости выключи рассылку в настройках.", [
                [("📸 Ещё тестовый мем", "v2:photo_test"),
                 ("⚙️ Настройки, вацок", "v2:settings")],
                [("🏠 На базу", "v2:home")],
            ])
            return await real_memes.send(q.message, uid, substance,
                                            risk_from_titles(titles), connect,
                                            meme_key, record_meme, meme_buttons)

        if action == "photo_test":
            with connect() as c:
                settings, session = setup_state(c, uid)
                substance = session["substance"] if session else settings["last_substance"]
                titles = [x["title"] for x in picked(c, uid)]
            return await real_memes.send(q.message, uid, substance,
                                            risk_from_titles(titles), connect,
                                            meme_key, record_meme, meme_buttons)

        if action == "impulses":
            return await panel(q, "🔥 ОСТАНОВИ МЕНЯ\n\nЧто сейчас больше всего тянет сделать?\n"
                               "Нажми один вариант — без длинных анкет.", [
                *[[(label, f"v2:hold:{tag}")] for tag, label in safety.IMPULSES],
                [("🆘 Физически плохо", "v2:safety"), ("🏠 На базу", "v2:home")],
            ])

        if action == "hold":
            if param not in safety.IMPULSE_ADVICE:
                return await panel(q, "Э, брат, выбери вариант из списка.", [[("🏠 На базу", "v2:home")]])
            with connect() as c:
                settings, session = setup_state(c, uid)
                substance = session["substance"] if session else settings["last_substance"]
                db.record_event(c, uid, session["id"] if session else None, "risk", param)
            await panel(q, safety.impulse_message(param, substance), [
                [("⏳ Проверить через 10 мин", "v2:wait:urge"), ("✅ Передумал", "v2:better")],
                [("🔔 Напоминай чаще, брат", "v2:boost"), ("🆘 Мне плохо", "v2:safety")],
                [("🏠 На базу", "v2:home")],
            ])
            if param != "paranoia":
                await real_memes.send(q.message, uid, substance, param, connect,
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
                    [("😂 Прикол", "v2:meme"), ("🔥 Меня несёт", "v2:impulses")],
                    [("🆘 Мне плохо", "v2:safety"), ("🏠 На базу", "v2:home")],
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
                [("🔥 Ещё нужна поддержка", "v2:impulses"), ("🏠 На базу", "v2:home")],
            ])

        if action == "boost":
            with connect() as c:
                settings, session = setup_state(c, uid)
                if not session:
                    return await panel(q, "Сначала включи сопровождение, вацок.", [[("▶️ Погнали, вацок", "v2:start")]])
                until = stamp(now() + timedelta(hours=1))
                db.set_setting(c, uid, boost_until=until, quiet=0)
                db.schedule_alert(c, uid, session["id"], 10, now())
            return await panel(q, "⚡ На ближайший час включены напоминания примерно каждые 10 минут.\n"
                               "Выключить их можно в «Настройках».",
                               [[("⚙️ Настроить, брат", "v2:settings"), ("🏠 На базу", "v2:home")]])

        if action == "wait":
            kind = param if param in ("urge", "wellbeing", "after") else "urge"
            delay = 480 if kind == "after" else 10
            with connect() as c:
                db.schedule_followup(c, uid, kind, delay, now())
            return await panel(q,
                ("☀️ Напомню проверить себя примерно через 8 часов." if kind == "after" else
                 "⏳ Напишу через 10 минут, чтобы проверить состояние.\n"
                 "Если становится физически хуже — не жди уведомления, обращайся за помощью."), [
                    [("✅ Передумал", "v2:better"), ("🆘 Мне плохо", "v2:safety")],
                    [("🏠 На базу", "v2:home")],
                ])

        if action == "checkin":
            return await panel(q, "🫀 КАК ТЫ?\n\nВыбирай, что ближе прямо сейчас. "
                               "Это не диагноз — ответы помогут подобрать действие.", [
                [("👌 В порядке", "v2:feel:ok"), ("😰 Паника", "v2:feel:panic")],
                [("🔥 Тянет натворить делов", "v2:impulses"), ("😵 Не сплю", "v2:hold:sleep")],
                [("🤢 Физически плохо", "v2:safety"), ("🚨 Срочно", "v2:emergency")],
                [("🏠 На базу", "v2:home")],
            ])

        if action == "safety":
            return await panel(q,
                "🆘 ШТАБ ПОМОЩИ МАГИ\n\n"
                "Брат, сначала оценим безопасность. Здесь без осуждения.\n"
                "Боль в груди, тяжело дышать, судороги, потеря сознания, "
                "сильный перегрев или спутанность — нужна скорая.",
                [
                    [("🚨 Экстренно · 112", "v2:emergency")],
                    [("💊 Выбрать вещество", "v2:prepchoose")],
                    [("🧘 Прийти в себя", "v2:selfhelp"),
                     ("🩺 Проверить симптомы", "v2:questions")],
                    [("🌙 Перед тяжёлой ночью", "v2:nightprep"),
                     ("☀️ Восстановление", "v2:recovery")],
                    [("🏠 На базу", "v2:home")],
                ])

        if action == "prepchoose":
            rows = [[(name, f"v2:prep:{i}")]
                    for i, name in enumerate(safety.SUBSTANCES)]
            rows.append([("⬅️ К помощи", "v2:safety")])
            return await panel(q,
                "💊 ПОМОЩЬ ПО ВЕЩЕСТВУ\n\n"
                "Выбери вещество, чтобы увидеть основные риски и "
                "признаки опасного состояния. Без инструкций по употреблению.",
                rows)

        if action == "prep":
            try:
                i = int(param)
                if i < 0 or i >= len(safety.SUBSTANCES):
                    raise ValueError()
                substance = safety.SUBSTANCES[i]
            except (ValueError, IndexError):
                return await panel(q, "Выбери вещество из списка.",
                    [[("⬅️ К списку", "v2:prepchoose")]])
            return await panel(q, safety.preparation_card(substance), [
                [("🧘 Самопомощь", "v2:selfhelp"),
                 ("🩺 Симптомы", "v2:questions")],
                [("⬅️ К веществам", "v2:prepchoose"),
                 ("🆘 Помощь", "v2:safety")],
            ])

        if action == "selfhelp":
            return await panel(q,
                "🧘 САМОПОМОЩЬ · ПЕРВЫЕ МИНУТЫ\n\n"
                "1. Остановись. Не принимай больше ничего и не смешивай вещества.\n"
                "2. Перейди в тихое, прохладное место, сядь.\n"
                "3. Дыши спокойно, не заставляй себя делать глубокие вдохи.\n"
                "4. Если можешь безопасно глотать — пей воду небольшими глотками.\n"
                "5. Свяжись с трезвым человеком. Не садись за руль.\n\n"
                "При боли в груди, одышке, судорогах, обмороке, "
                "сильном перегреве или спутанности — не жди, вызывай помощь.",
                [
                    [("😰 Паника", "v2:feel:panic"),
                     ("🩺 Другие симптомы", "v2:questions")],
                    [("🚨 Экстренно · 112", "v2:emergency")],
                    [("⬅️ К помощи", "v2:safety"),
                     ("🏠 На базу", "v2:home")],
                ])

        if action == "nightprep":
            return await panel(q,
                "🌙 ПЕРЕД ТЯЖЁЛОЙ НОЧЬЮ\n\n"
                "Самый безопасный вариант — не начинать и отложить решение.\n\n"
                "Если рискуешь оказаться в сложной ситуации: "
                "заранее договорись с трезвым человеком о связи, "
                "заряди телефон, оставь доступ к воде и прохладному месту, "
                "не планируй поездки за рулём.\n\n"
                "Не смешивай вещества, алкоголь или лекарства без назначения. "
                "Не оставайся один, если чувствуешь себя плохо.\n"
                "Марафон и бессонница повышают риск перегрева, "
                "судорог и проблем с сердцем. Лучше остановиться и отдохнуть.",
                [
                    [("🛑 Остановиться на 10 минут", "v2:impulses")],
                    [("🧘 Если уже нехорошо", "v2:selfhelp"),
                     ("🚨 Экстренно · 112", "v2:emergency")],
                    [("⬅️ К помощи", "v2:safety")],
                ])

        if action == "recovery":
            return await panel(q,
                "☀️ ВОССТАНОВЛЕНИЕ\n\n"
                "Отдыхай в спокойном месте, поешь доступную привычную еду, "
                "пей воду по жажде. Не пытайся уснуть алкоголем, "
                "снотворным без назначения или новой дозой вещества.\n\n"
                "После длительной бессонницы и употребления могут "
                "усиливаться тревога и подавленность. Попроси поддержки "
                "у близкого человека и обратись за медицинской помощью, "
                "если состояние не улучшается.\n\n"
                "Боль в груди, затруднение дыхания, судороги, "
                "потеря сознания или сильная спутанность — вызывай скорую.",
                [
                    [("🧘 Прийти в себя", "v2:selfhelp"),
                     ("🚨 Экстренно · 112", "v2:emergency")],
                    [("⬅️ К помощи", "v2:safety"),
                     ("🏠 На базу", "v2:home")],
                ])

        if action in ("faq", "meds"):
            return await choose_safety(q)

        if action == "med":
            try:
                substance = safety.SUBSTANCES[int(param)]
                if int(param) < 0:
                    raise IndexError
            except (ValueError, IndexError):
                return await panel(q, "Неизвестное вещество.", [[("⬅️ Обратно, брат", "v2:safety")]])
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
                [("⬅️ Другие симптомы", "v2:questions"), ("🏠 На базу", "v2:home")],
            ])

        if action == "questions":
            return await show_symptoms(q)

        if action == "emergency":
            return await panel(q,
                "🚨 ЭКСТРЕННАЯ ПОМОЩЬ\n\n"
                "📞 Открой «Телефон» и набери: Россия и ЕС — 112, США и Канада — 911. "
                "В другой стране — местный номер экстренных служб.\n\n"
                "Скажи диспетчеру: адрес, симптомы, что и когда принимал "
                "(если знаешь).\n\n"
                "Пока едут: не садись за руль, не оставайся один. "
                "Если человек без сознания, но дышит — поверни на бок. "
                "Если не дышит нормально — выполняй инструкции диспетчера. "
                "При судорогах ничего не клади в рот.\n\n"
                "Боль в груди, одышка, судороги, обморок, сильный "
                "перегрев — вызывай помощь сразу.",
                [
                    [("🩺 Симптомы", "v2:questions"),
                     ("⬅️ К помощи", "v2:safety")],
                ])

        if action == "memethemes":
            return await panel(q, "🦅 МЕМНЫЙ ОТДЕЛ МАГИ\n\nВыбирай тему, вацок. Фото и приколы — как раньше!", [
                [("🎭 Жизненные", "v2:memerisk:general"),
                 ("🔥 Не удержался", "v2:memerisk:more")],
                [("💔 Бывшая", "v2:memerisk:ex"),
                 ("📞 Звонки", "v2:memerisk:contacts")],
                [("💸 Деньги", "v2:memerisk:money"),
                 ("🌙 Без сна", "v2:memerisk:sleep")],
                [("🚕 Приключения", "v2:memerisk:travel"),
                 ("👀 Тревога", "v2:memerisk:paranoia")],
                [("🎲 Рандом, брат", "v2:meme"),
                 ("🏠 На базу", "v2:home")],
            ])

        if action == "memerisk":
            if param not in real_memes.LINES:
                return await panel(q, "Тема не найдена.", [[("🏠 На базу", "v2:home")]])
            with connect() as c:
                settings, session = setup_state(c, uid)
                substance = session["substance"] if session else settings["last_substance"]
            return await real_memes.send(q.message, uid, substance, param, connect)

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
            return await real_memes.send(q.message, uid, substance, risk, connect,
                                            meme_key, record_meme, meme_buttons)

        if action == "stats":
            return await send_chart(q.message, uid)

        if action == "settings":
            import math
            with connect() as c:
                settings, session = setup_state(c, uid)
                alert = c.execute("SELECT * FROM alerts WHERE user_id=?", (uid,)).fetchone()
            if not session:
                status = "🔕 Сессия не запущена. Напоминания не отправляются."
            elif not alert or alert["session_id"] != session["id"]:
                status = "⚠️ Нет расписания для активной сессии. Ничего не отправляется."
            elif settings["quiet"]:
                status = "⏸ Тихий режим — рассылка отключена."
            else:
                from datetime import datetime, timezone
                try:
                    next_at = datetime.fromisoformat(alert["next_at"])
                    if next_at.tzinfo is None:
                        next_at = next_at.replace(tzinfo=timezone.utc)
                    remain = math.ceil((next_at - now()).total_seconds() / 60)
                    status = (f"🔔 Каждые {alert['interval_min']} минут.\n"
                              f"Следующая отправка примерно через {max(0, remain)} мин.")
                except (TypeError, ValueError):
                    status = "⚠️ Ошибка расписания. Включи 10 минут заново."
            rows = [
                [("10 минут", "v2:setfreq:10"), ("15 минут", "v2:setfreq:15")],
                [("30 минут", "v2:setfreq:30"), ("60 минут", "v2:setfreq:60")],
                [("🔔 Включить" if settings["quiet"] else "⏸ Выключить",
                  "v2:quiet")],
                [("⚡ Усилить на час", "v2:boost"),
                 ("⬅️ Настройки", "ui:mine")],
            ]
            if not session:
                rows.insert(0, [("💀 Включить сопровождение", "v2:start")])
            return await panel(q, "🦅 МАГА | НАПОМИНАЛКИ\n\n" + status +
                               "\n\nНужна тишина — выключи рассылку кнопкой.\n"
                               "Вернуться можно в любой момент, вацок.",
                               rows)

        if action == "quiet":
            with connect() as c:
                settings, session = setup_state(c, uid)
                new_value = 0 if settings["quiet"] else 1
                db.set_setting(c, uid, quiet=new_value, boost_until=None)
                if not new_value and session:
                    minutes = int(settings["base_interval"])
                    db.schedule_alert(c, uid, session["id"], minutes, now())
            return await panel(q, "⏸ Напоминания выключены." if new_value else
                               "⏰ Напомни, брат снова включены.", [
                [("🔔 Напоминания", "v2:settings"), ("⚙️ Все настройки", "ui:mine")],
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
                [("☀️ Спроси через 8 часов", "v2:wait:after")],
                [("📊 Расклад", "v2:stats"), ("🏠 На базу", "v2:home")],
            ])

        if action == "post":
            tag = "better" if param == "good" else "bad"
            with connect() as c:
                db.record_event(c, uid, None, "state", tag)
            return await panel(q, "Отметка сохранена. Спасибо, что проверил своё состояние.\n"
                               "Сделай паузу и не принимай важных решений на автомате.", [
                [("📊 Моя статистика", "v2:stats"), ("🆘 Мне плохо", "v2:safety")],
                [("🏠 На базу", "v2:home")],
            ])

        return await panel(q, "Этот пункт недоступен.", [[("🏠 На базу", "v2:home")]])
