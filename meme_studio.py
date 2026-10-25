"""Original, automatically publishable Russian dark-humour meme studio.

All captions and graphical layouts in this module are written for this bot.
No third-party image, celebrity likeness, API key, or scraping is needed.
A finite curated joke catalog is not an AI and does not invent endless new jokes.
"""
from __future__ import annotations

import hashlib
import os
import random
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
MEDIA = ROOT / "assets" / "memes"
SIZE = 960
_LOCK = threading.Lock()

@dataclass(frozen=True)
class Joke:
    category: str
    label: str
    setup: str
    punchline: str

# Format: category | label | setup | punchline. Original, intentionally ironic
# jokes about bad decisions, consequences, and recovery; no use instructions.
_RAW = """
general|МЕФ И ПЛАНЫ|Я: завтра начинаю новую жизнь|Мозг в 03:17: сначала пересмотрим все ошибки с 2016-го
general|СОЛЕВОЙ МАРКЕТИНГ|Обещали расширение сознания|Расширился только список проблем
general|ПАША ТЕХНИК: ЭФФЕКТ|Когда хотел быть легендой андеграунда|А стал легендой районного чата «Кто опять шумит?»
general|КОКАИНОВЫЙ БЮДЖЕТ|Планы на месяц: квартира, отдых, еда|Финансовый отдел мозга: смешная фантастика
general|ВЕЧЕРНИЙ БРИФИНГ|Здравый смысл покинул совещание|Остались тревога, банкир и три плохие идеи
general|МДМА И РОМАНТИКА|Полюбил весь мир на одну ночь|Наутро мир попросил больше ему не писать
general|ХРОНИКА РЕШЕНИЙ|Когда обещал себе «всё под контролем»|Контроль тем временем уволился без отработки
general|АЛЬФА-ПВП: СТАТИСТИКА|В голове пятьдесят гениальных планов|Ни один не прошёл проверку реальностью
general|ТЕОРИЯ ЗАГОВОРА|В 04:40 начал понимать устройство мира|В 09:00 не смог понять устройство чайника
general|ВЫСШАЯ МАТЕМАТИКА|В уме решал судьбу вселенной|На кассе не хватило на воду
general|НАРКОМАНСКИЙ HORROR|Самый страшный монстр ночи|Сообщение от банка: «Недостаточно средств»
general|СКОРОСТЬ|Говорили: «Надо двигаться вперёд»|Не уточнили, что можно просто поспать
general|КАННАБИС: ДИПЛОМАТИЯ|Пять минут выбирал фильм|Устал. Решил посмотреть меню
general|ЛСД И ФИЛОСОФИЯ|Открыл для себя новые измерения|Старые обязанности остались в этом
general|КЕТАМИН И БЫТ|В мыслях парил над реальностью|В реальности закончилась туалетная бумага
general|ГЕРОИЧЕСКИЙ СЮЖЕТ|Хотел драматичный сюжет как в кино|Получил драму с банковским приложением
general|МЕФЕДРОН: ГЕОГРАФИЯ|Искал смысл жизни всю ночь|Утром нашёл только чужую зарядку
general|ПОСЛЕ ТУСОВКИ|Телефон: 2% заряда|Репутация: обновление недоступно
general|ПСИХОДЕЛИЧЕСКИЙ ДИЗАЙН|Вселенная послала мне знак|Это был счёт за коммуналку
general|МЕМ ПРО ВЗРОСЛЕНИЕ|В 18: свобода и безумие|В 25: свобода отменить все планы и лечь спать
general|РАЙОННАЯ ЛЕГЕНДА|У него была мечта покорить мир|Но сперва пришлось покорить очередь в МФЦ
general|РАЗГОВОР С СОБОЙ|— Кто отвечает за мои решения?|— Пользователь вышел из чата
general|БАНКОВСКИЙ ТРИЛЛЕР|Самая напряжённая сцена ночи|Открыть историю переводов
general|ТЁМНАЯ СТОРОНА|Ночь обещала незабываемые впечатления|Память решила не участвовать
general|ПЛАН «НЕ НАТВОРИ»|Мне нужен был знак остановиться|Бот: вот он. Уже третий за вечер
general|СЛАБОСТЬ ГЛАВНОГО ГЕРОЯ|Я могу победить любого босса|Кроме уведомления «Вы сегодня спали 2 часа»
general|ОТКРЫТЫЕ ВКЛАДКИ|В голове открыто сорок вкладок|И в одной играет тревога, но где — непонятно
general|ПАША ТЕХНИК: КУЛЬТУРА|Жизнь хотела рэп-баттл|А я записался на курс по управлению последствиями
general|ЧЁРНАЯ КОМЕДИЯ|Когда кажется, что ниже уже некуда|Банк предлагает увеличить кредитный лимит
general|СОЛЬ НА РАНУ|Попросил у жизни немного драйва|Жизнь прислала повестку от стоматолога
general|ПСИХОАКТИВНЫЙ ОФИС|Мозг: «Можем ещё поработать»|Организм: «Мы вообще-то тут заложники»
general|МЕФЕДРОН: ЛАЙФСТАЙЛ|На фото тусовки — главный герой|В выписке банка — главный подозреваемый
general|ПОНЕДЕЛЬНИК|План: стать лучшей версией себя|Реальность: найти чистые носки и не сойти с ума
general|ПЕРЕОЦЕНКА ЦЕННОСТЕЙ|Вчера казалось, что всё имеет смысл|Сегодня смысл — стакан воды и тишина
general|КОМИТЕТ ПО РИСКАМ|Я: «А что может пойти не так?»|Комитет: «У нас презентация на 84 слайда»
general|МДМА: ОФИЦИАЛЬНО|Душа просила большой любви|Нервная система подала заявление на отпуск
general|ПРОЗРЕНИЕ|Лучшие идеи приходят ночью|Худшие тоже. Только громче
general|КАННАБИС И КИНО|Собрался смотреть сложное кино|Полчаса изучал логотип студии
general|РЕЖИМ ВЫЖИВАНИЯ|Моя цель на сегодня — ничего не усложнять|Мой характер: интересный вызов
general|СОЛЕВАЯ АНАЛИТИКА|Миллион версий, кто виноват|Ни одной — зачем не ложился спать
general|НОЧНАЯ ЛОГИКА|Если проблема не решается|Можно хотя бы не создавать ещё четыре
general|ЧЁРНЫЙ ЮМОР|Сказали: «Завтра будет новый день»|А где оформить возврат за предыдущий?
general|МИНИ-СЕРИАЛ|Первый сезон: «Я контролирую ситуацию»|Второй сезон: «Мам, можно поговорить?»
general|ПСИХИЧЕСКИЙ ZIP|Сжал тревогу в маленький архив|Она распаковалась на весь рабочий стол
general|АМФЕТАМИНОВЫЙ ДЕДЛАЙН|В голове успел закончить десять проектов|В календаре по-прежнему вчера
general|ЛСД: СЛУЖБА ПОДДЕРЖКИ|— Я нашёл истину!|— Отлично. Теперь найдите ключи от квартиры
general|НОЧНАЯ ЭКОНОМИКА|Вселенная дала мне шанс одуматься|С комиссией за перевод
general|МЕФЕДРОН И МОТИВАЦИЯ|Чувствовал себя главным инвестором года|Утром изучал цены на гречку
general|ВНУТРЕННИЙ СОВЕТ|Разум: «Давай домой»|Глупость: «У меня есть ещё один аргумент»
general|БОЛЬШОЙ ФИНАЛ|Главный герой устал от приключений|Авторы продлили сериал из-за долгов
ex|БЫВШАЯ: ПОСЛЕДНЯЯ СЕРИЯ|02:48. «Напишу просто узнать как дела»|Это уже восьмой финальный сезон
ex|РОМАНТИКА ИЗ АДА|Мозг предлагает написать бывшей|Самоуважение срочно эвакуируется
ex|НАСТОЯЩИЙ ХОРРОР|Самая страшная кнопка в телефоне|«Отправить» в чате, который надо было удалить
ex|ПРОШЛОЕ ЗВОНИТ|Соскучился по бывшей в три ночи|Утром соскучился по своей репутации
ex|ЭКСПЕРИМЕНТ|Проверил, скучает ли бывшая|Теперь проверяю, как работает блокировка
ex|МЕФ И НОСТАЛЬГИЯ|Вспомнил «ту самую любовь»|Она вспомнила номер участкового
ex|СЕРДЕЧНАЯ АРХЕОЛОГИЯ|Раскопал старую переписку|Нашёл древние доказательства глупости
ex|ОБРАТНЫЙ ОТСЧЁТ|До отправки сообщения: 3... 2...|Здоровый смысл перерезал провод
ex|СЮЖЕТНЫЙ ПОВОРОТ|Думал, что скучаю по человеку|Оказалось, просто пора спать
ex|ОСКАР ЗА ДРАМУ|Напечатал монолог на 14 экранов|Удалил. Получил роль взрослого человека
ex|ЗАМКНУТЫЙ КРУГ|Захотел вернуть старые отношения|Напомнил себе, зачем они старые
ex|НАДЁЖНЫЙ ПЛАН|Если я ей не напишу сегодня|Завтра буду благодарен себе бесплатно
calls|ГОЛОСОВЫЕ СООБЩЕНИЯ|Записал другу мысль на 11 минут|Друг прислал номер горячей линии
calls|НЕ ЗВОНИ|В 04:00 появился повод всем позвонить|Повод называется «не спал»
calls|ГЕНИАЛЬНАЯ ИДЕЯ|Хотел собрать всех друзей срочно|Друзья уже собрали чат «Он опять?»
calls|ИНФОРМАЦИОННЫЙ ШУМ|Мозг: «Позвони всем, расскажи правду»|Палец: «Включаю авиарежим»
calls|ПОДКАСТ НА НОЧЬ|Я: «Сейчас быстро объясню»|Счётчик голосового: 18:42
calls|СОЦИАЛЬНАЯ СЕТЬ|У меня было пять очень важных новостей|Ни одну не стоило отправлять
calls|ЗВОНКИ В ПУСТОТУ|Когда хочется кого-то разбудить|Вспомни, как любишь спать сам
calls|ПАША ТЕХНИК В ПЛЕЙЛИСТЕ|Настроение как на громком концерте|Соседи предпочли акустическую тишину
calls|ЧАТ ВЫЖИВШИХ|Ночью написал всем «ты не спишь?»|Утром никто со мной не разговаривал
calls|ДИАЛОГ С РЕАЛЬНОСТЬЮ|— Что такого в одном звонке?|— Посмотри историю звонков
calls|НАРУШЕНИЕ ТИШИНЫ|Всем казалось, что я исчез|Они просто наконец выспались
calls|ЛУЧШИЙ ДРУГ|Мой друг в три ночи — не телефон|А кнопка «Не беспокоить»
money|МЕФ И ФИНАНСЫ|Запланировал инвестировать в будущее|Проинвестировал в воспоминания, которых нет
money|ДОЛГОСРОЧНЫЙ ПРОЕКТ|Кошелёк: «Давай копить»|Импульс: «Я быстро, только на минуту»
money|ПРИВЕТ ОТ БАНКА|Ночь прошла незабываемо|Банк тоже запомнил и всё записал
money|КАССОВЫЙ РАЗРЫВ|Мозг хочет приключений|Карточка просит программу реабилитации
money|ГЕНИЙ ЭКОНОМИКИ|Уверенно планировал стать богатым|Подсчитываю кешбэк с лапши
money|НАЛОГ НА ГЛУПОСТЬ|Думал, судьба выставит счёт позже|Счёт пришёл сразу. И с процентами
money|ИНВЕСТОР ГОДА|На словах — криптомиллионер|В приложении — «У вас 14 рублей»
money|ФИНАНСОВЫЙ УЖАСТИК|Открыл банковское приложение|Скример был без предупреждения
money|АЛЬФА-ПВП И МЕЧТЫ|Утром хотел купить весь мир|Вечером ищу скидку на макароны
money|КАРТА ЖЕЛАНИЙ|Хотел, чтобы деньги работали на меня|Они уволились без выходного пособия
money|ФИНАНСОВЫЙ САМОКОНТРОЛЬ|Не купил ничего лишнего сегодня|Нобелевскую премию переведите на карту
money|БЮДЖЕТНЫЙ ДЕТЕКТИВ|Где пропали деньги?|У следствия очень неприятные подозрения
travel|ПОЕХАЛИ?|В голове начался роуд-муви|В реальности лучше вызвать такси и домой
travel|НОЧНОЙ МАРШРУТ|Я: «Сейчас куда-нибудь сорвусь»|Навигатор: «Лучший пункт назначения — кровать»
travel|ГЕРОЙ БОЕВИКА|Хотел приключений на всю ночь|Получил приключение с поиском ключей
travel|НЕ ЗА РУЛЬ|Мозг предложил эффектный выезд|Ответственный взрослый отменил съёмки
travel|ПОТЕРЯННЫЙ ГОРОД|По дороге к великим свершениям|Забыл, зачем вышел из дома
travel|ПОЗДНИЙ ВЫЛЕТ|Планы звучали как мировое турне|Организм объявил задержку рейса
travel|ВЕЛИКИЙ ПУТЬ|Ноги зовут куда-то в даль|Подушка просит вернуться к семье
travel|АДРЕНАЛИН|Собирался прокатиться на эмоциях|Эмоции теперь требуют страховку
travel|ТАКСИ В РЕАЛЬНОСТЬ|Пункт А: плохая идея|Пункт Б: домой, без промежуточных трагедий
travel|ГОРОД НЕ СПИТ|Ночной город обещал чудеса|Дома обещали горячий чай и отсутствие проблем
travel|АВТОПИЛОТ|Когда решил, что «нормально доеду»|Нет. Вызови такси. Финал без спецэффектов
travel|ПУТЕШЕСТВИЕ ВО ВРЕМЕНИ|Хотел вернуть вчерашний вечер|Получилось только отменить завтрашние планы
""".strip()

def catalog():
    jokes = []
    seen = set()
    for line in _RAW.splitlines():
        parts = tuple(p.strip() for p in line.split("|"))
        if len(parts) != 4 or any(not part for part in parts):
            raise ValueError("Malformed original joke: " + line[:60])
        joke = Joke(*parts)
        if joke.category not in ("general", "ex", "calls", "money", "travel"):
            raise ValueError("Unknown category")
        if (joke.setup, joke.punchline) in seen:
            raise ValueError("Duplicate original joke")
        seen.add((joke.setup, joke.punchline))
        jokes.append(joke)
    return tuple(jokes)

THEMES = (
    ("#10151f", "#fa4254", "#f4f4ed", "#222e3a"),  # dark punch
    ("#e9e7de", "#b52d38", "#171b27", "#fffdfa"),  # newspaper
    ("#101a19", "#28db9f", "#ecfff8", "#18332e"),  # terminal
    ("#27122b", "#ffcb53", "#ffedf2", "#481d42"),  # night club
    ("#12253d", "#71cfff", "#f4faff", "#1c4057"),  # late-night
    ("#321b19", "#f7a46c", "#fff2e2", "#533027"),  # warning
)

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "DejaVuSans.ttf",
)

def font(size):
    for candidate in FONT_CANDIDATES:
        try:
            f = ImageFont.truetype(candidate, size)
            # Do not silently draw unreadable boxes if a font lacks Cyrillic.
            if f.getmask("Проверка").getbbox():
                return f
        except (OSError, ValueError):
            pass
    raise RuntimeError("Install a Cyrillic font (e.g. fonts-dejavu-core)")

def text_width(draw, text, f):
    box = draw.textbbox((0, 0), text, font=f)
    return box[2] - box[0]

def wrap(draw, text, f, max_width):
    lines = []
    current = ""
    for word in text.split():
        probe = (current + " " + word).strip()
        if text_width(draw, probe, f) <= max_width:
            current = probe
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines

def fit(draw, text, width, max_lines, start=74, floor=35):
    for size in range(start, floor - 1, -2):
        f = font(size)
        lines = wrap(draw, text, f, width)
        if len(lines) <= max_lines and all(text_width(draw, line, f) <= width for line in lines):
            return f, lines
    raise ValueError("Caption cannot fit: " + text[:45])

def render(joke, output):
    """Render an original, readable JPEG. Existing files are never overwritten."""
    digest = hashlib.sha256(("|".join((joke.category, joke.label, joke.setup, joke.punchline))).encode()).hexdigest()
    theme = THEMES[int(digest[:4], 16) % len(THEMES)]
    bg, accent, ink, panel = theme
    image = Image.new("RGB", (SIZE, SIZE), bg)
    draw = ImageDraw.Draw(image)
    # Vector-like card layouts, no stock or third-party photographs.
    for i in range(0, SIZE, 45):
        draw.line((0, i, SIZE, i), fill=panel, width=1)
    draw.rounded_rectangle((45, 45, SIZE - 45, SIZE - 45), radius=42, fill=panel, outline=accent, width=6)
    draw.rounded_rectangle((74, 77, SIZE - 74, 158), radius=22, fill=accent)
    badge = font(29)
    draw.text((101, 97), "НЕ НАТВОРИ ДЕЛОВ", font=badge, fill=bg)
    draw.text((SIZE - 155, 104), "18+", font=font(24), fill=bg)
    label_font, label_lines = fit(draw, joke.label.upper(), SIZE - 185, 2, start=45, floor=28)
    label_y = 199
    for line in label_lines:
        draw.text((SIZE // 2, label_y), line, fill=accent, font=label_font, anchor="ma", align="center")
        label_y += label_font.size + 9
    top_font, top_lines = fit(draw, joke.setup, SIZE - 178, 3, start=67, floor=38)
    upper_y = 340
    for line in top_lines:
        draw.text((SIZE // 2, upper_y), line, fill=ink, font=top_font, anchor="ma")
        upper_y += top_font.size + 12
    draw.rounded_rectangle((170, 560, SIZE - 170, 569), radius=4, fill=accent)
    punch_font, punch_lines = fit(draw, joke.punchline, SIZE - 178, 4, start=68, floor=34)
    lower_y = 630
    for line in punch_lines:
        draw.text((SIZE // 2, lower_y), line, fill=ink, font=punch_font, anchor="ma")
        lower_y += punch_font.size + 13
    draw.text((SIZE // 2, 887), "ЧЁРНЫЙ ЮМОР  •  ОРИГИНАЛЬНЫЙ МЕМ", fill=accent,
              font=font(23), anchor="ma")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".tmp")
    try:
        image.save(tmp, format="JPEG", quality=89, optimize=True)
        os.replace(tmp, output)
    finally:
        tmp.unlink(missing_ok=True)
    return output

def meme_path(joke, media_dir=MEDIA):
    seed = "|".join((joke.category, joke.label, joke.setup, joke.punchline))
    key = hashlib.sha256(seed.encode()).hexdigest()[:20]
    return Path(media_dir) / joke.category / ("original_" + key + ".jpg")

def ensure_catalog(media_dir=MEDIA, limit=None):
    """Idempotently populate the local library; safe to call on startup or /meme."""
    with _LOCK:
        result = []
        jokes = catalog()
        if limit is not None:
            jokes = jokes[:limit]
        for joke in jokes:
            path = meme_path(joke, media_dir)
            if not path.is_file() or path.stat().st_size == 0:
                render(joke, path)
            result.append(path)
        return result

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "count":
        print("Original jokes:", len(catalog()))
    else:
        paths = ensure_catalog()
        print(f"Original meme images ready: {len(paths)} in {MEDIA}")
