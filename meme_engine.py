"""A freshly drawn, context-matched picture meme for EVERY user request.

No image service, purchased API key, scraped photo, or disk write is needed.
Original parody sea-creature archetypes (not exact TV character copies) have
changing facial expressions, props, poses, scenes, jokes and visual palettes.

The monotonically increasing per-user issue number is reserved in SQLite.
Even if the random seed repeats, two successive sends have different keys and
different pictured issue marks. An API failure never gets marked as "sent".
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import random
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from PIL import Image, ImageDraw
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from photo_memes import font

SIZE = 850
SUBS = {
    "Мефедрон": "МЕФ", "Альфа-ПВП": "АЛЬФА", "Амфетамин": "СКОРОСТЬ",
    "Метамфетамин": "СТИМУЛЯТОР", "Кокаин": "КОКАИН", "MDMA": "МДМА",
    "Каннабис": "ТРАВА", "Алкоголь": "АЛКОГОЛЬ", "Кетамин": "КЕТАМИН",
    "Опиоиды": "ОПИОИДЫ", "Бензодиазепины": "ТАБЛЕТКИ",
    "ЛСД": "ПСИХОДЕЛИКИ", "Псилоцибин": "ПСИХОДЕЛИКИ",
}
RISK_TEXT = {
    "more": (
        ("Я: СЕГОДНЯ БЕЗ ПРОДОЛЖЕНИЯ", "МОЗГ: А КАК ЖЕ ВТОРОЙ СЕЗОН?"),
        ("КОГДА ЗАХОТЕЛОСЬ ЕЩЁ", "МУДРАЯ МЫСЛЬ: ДАВАЙ БЕЗ ПРОДОЛЖЕНИЯ"),
        ("ПОСЛЕДНИЙ РАЗ, ГОВОРИЛИ ОНИ", "ПОСЛЕДНИЙ СЕЗОН, ГОВОРИЛ NETFLIX"),
        ("МОЗГ ПРЕДЛАГАЕТ НОВЫЙ КВЕСТ", "Я ПРЕДЛАГАЮ ПЕРЕРЫВ"),
        ("«ТОЛЬКО ЕЩЁ ЧУТЬ-ЧУТЬ»", "САМАЯ ПОДОЗРИТЕЛЬНАЯ ФРАЗА НОЧИ"),
        ("КОГДА ХОЧЕТСЯ ПРОДОЛЖИТЬ", "ВСПОМНИ, КАК ВЫГЛЯДИТ ЗАВТРА"),
        ("ОЧЕРЕДНАЯ ГЕНИАЛЬНАЯ ИДЕЯ", "ПУСТЬ ПОЛЕЖИТ В ЧЕРНОВИКАХ"),
        ("МОЗГ: НУ ДАВАЙ ЕЩЁ", "КРАБ-БУХГАЛТЕР: МЫ ЗАКРЫТЫ"),
    ),
    "ex": (
        ("Я В 04:19: ЕЙ НАДО НАПИСАТЬ", "ТЕЛЕФОН: БЛЯТЬ, НЕ НАДО"),
        ("КОГДА ОТКРЫЛ ЧАТ С БЫВШЕЙ", "ДАЖЕ МОРСКАЯ ЗВЕЗДА ИСПУГАЛАСЬ"),
        ("МОЗГ: НАПИШИ «ПРИВЕТ»", "ПАМЯТЬ: ТЫ УЖЕ ПИСАЛ ВЧЕРА"),
        ("НОЧНОЙ РОМАНТИК У ТЕЛЕФОНА", "УТРОМ БУДЕТ ДОКУМЕНТАЛКА"),
        ("СМС БЫВШЕЙ В 5 УТРА", "ВЕЛИКАЯ ИДЕЯ? НЕТ, СПАСИБО"),
        ("КОГДА ПОЧТИ НАЖАЛ «ОТПРАВИТЬ»", "ПОМОГ ТОЛЬКО РЕЖИМ ПОЛЁТА"),
        ("Я: ПРОСТО УЗНАЮ КАК ОНА", "ЗДРАВЫЙ СМЫСЛ: ДОЖДИСЬ УТРА"),
    ),
    "contacts": (
        ("ПЯТОЕ ГОЛОСОВОЕ В 04:35", "ДРУЗЬЯ УЖЕ СПЯТ. ВОТ И ТЕБЕ БЫ"),
        ("Я: БРАТ, Я ТЕБЕ ОБЪЯСНЮ", "БРАТ: ТЫ УЖЕ 7 РАЗ ОБЪЯСНЯЛ"),
        ("МОЗГ ПРЕДЛАГАЕТ ВСЕМ ПОЗВОНИТЬ", "ТЕЛЕФОН ПРОСИТ ВЫХОДНОЙ"),
        ("КОГДА ХОЧЕШЬ ВСЕМ ВСЁ СКАЗАТЬ", "ЗАПИШИ В ЗАМЕТКИ И ЗАБЕЙ"),
        ("У МЕНЯ ЕСТЬ ГЕНИАЛЬНАЯ МЫСЛЬ", "НО ПУСТЬ ОНА ПОДОЖДЁТ УТРА"),
    ),
    "money": (
        ("Я: НАДО ЭКОНОМИТЬ", "ТАКЖЕ МОЙ МОЗГ: КУПИ ВООБЩЕ ВСЁ"),
        ("ОТКРЫЛ БАНКОВСКОЕ ПРИЛОЖЕНИЕ", "ХОРРОР СНЯТ БЕЗ СПЕЦЭФФЕКТОВ"),
        ("КРАБ СЧИТАЕТ МОИ ДЕНЬГИ", "ПРОИЗНОСИТ ОДНО: ХВАТИТ"),
        ("КОГДА ХОЧЕТСЯ ТРАТИТЬ", "ЗАКРОЙ БАНК. ЗАВТРА РАЗБЕРЁМСЯ"),
        ("НОЧНОЙ Я: ЭТО ВЫГОДНО", "УТРЕННИЙ Я: КОМУ?"),
    ),
    "travel": (
        ("Я: СЕЙЧАС КУДА-ТО ПОЕДУ", "МОЗГ: ЛУЧШЕ ПОСИДИ ДОМА"),
        ("КОГДА ЗАХОТЕЛОСЬ ПРИКЛЮЧЕНИЙ", "ТАКСИ ДОМОЙ — ВОТ ПРИКЛЮЧЕНИЕ"),
        ("Я В ГОЛОВЕ: ФОРСАЖ", "В РЕАЛЬНОСТИ: ЗА РУЛЬ НЕ НАДО"),
        ("ОПЯТЬ СОБРАЛСЯ НОЧЬЮ В ГОРОД", "МОРСКИЕ ЗВЁЗДЫ УЖЕ СПЯТ"),
        ("СПОНТАННЫЙ ПЛАН НА 04:00", "ОТЛОЖЕН ДО ТРЕЗВОГО УТРА"),
    ),
    "sleep": (
        ("МОЗГ В 04:58: ЕЩЁ ОДНА ИДЕЯ", "Я: МОЖНО ХОТЯ БЫ ПОСПАТЬ?"),
        ("КОГДА НЕ СПАЛ ВСЮ НОЧЬ", "И НАЧАЛ ОБСУЖДАТЬ УСТРОЙСТВО МИРА"),
        ("СОН ВЫШЕЛ ИЗ ЧАТА", "ЗАВТРА ПРИШЛЁТ ОФИЦИАЛЬНЫЙ СЧЁТ"),
        ("МОЗГ ОБЪЯВИЛ НОЧНУЮ ПЛАНЁРКУ", "НИКТО НЕ ГОЛОСОВАЛ ЗА ЭТО"),
        ("Я: ЛЯГУ И УСПОКОЮСЬ", "МОЗГ: А ПОМНИШЬ ПОЗОР В 2018?"),
    ),
    "paranoia": (
        ("Я: А ЧТО ТАМ ЗА ШУМ?", "ШТОРА: БРАТАН, Я ПРОСТО ШТОРА"),
        ("КОГДА ПОКАЗАЛОСЬ, ЧТО СЛЕДЯТ", "А ЭТО ХОЛОДИЛЬНИК РАБОТАЕТ"),
        ("В ГОЛОВЕ ТРИЛЛЕР НА 10 СЕРИЙ", "В РЕАЛЬНОСТИ НУЖНЫ ПОКОЙ И ПОМОЩЬ"),
        ("КОГДА КАЖЕТСЯ, ЧТО ВСЁ ПОДОЗРИТЕЛЬНО", "НЕ ПРИНИМАЙ РЕШЕНИЙ СЕЙЧАС"),
    ),
    "check": (
        ("БОТ: НЕ НАТВОРИ ДЕЛОВ", "Я: ПОПРОБУЮ БЕЗ СЮЖЕТНЫХ ПОВОРОТОВ"),
        ("ВЕЧЕР МОГ СТАТЬ ЛЕГЕНДОЙ", "НО ПУСТЬ БУДЕТ ПРОСТО ВЕЧЕРОМ"),
        ("ПЛАН НА НОЧЬ: БЕЗ ХЕРНИ", "ПОКА ВСЁ ИДЁТ ПО ПЛАНУ"),
        ("МОЗГ ПРЕДЛАГАЕТ ПРИКЛЮЧЕНИЯ", "ЗДРАВЫЙ СМЫСЛ ВЫЗЫВАЕТ ТАКСИ"),
    ),
    "general": (
        ("Я: ВСЁ ПОД КОНТРОЛЕМ", "КОНТРОЛЬ: Я ВООБЩЕ НЕ В КУРСЕ"),
        ("НОЧНОЙ Я: ЛЕГЕНДА", "УТРЕННИЙ Я: СВИДЕТЕЛЬ"),
        ("ОЧЕРЕДНАЯ ИДЕЯ В 4 УТРА", "СНАЧАЛА ПРОВЕРИМ ЕЁ УТРОМ"),
        ("КТО УСТРОИЛ ЭТОТ ЦИРК?", "РЕЖИССЁР УШЁЛ СПАТЬ"),
        ("ЗДРАВЫЙ СМЫСЛ НЕ ЗВОНИТ", "ОН ПРОСТО ЖДЁТ, ПОКА ТЫ ПЕРЕДУМАЕШЬ"),
    ),
    "after": (
        ("УТРО ПОСЛЕ ПРИКЛЮЧЕНИЙ", "ЗАГЛЯНУЛ В ТЕЛЕФОН. ЗРЯ"),
        ("Я ВЧЕРА: ГЕНИЙ", "Я СЕГОДНЯ: МНЕ НУЖНО ПОЛЕЖАТЬ"),
        ("НОЧЬ ОКОНЧЕНА", "ПУСТЬ И ГЛУПЫЕ РЕШЕНИЯ ОСТАНУТСЯ В НЕЙ"),
    ),
    "ok": (
        ("НИКОМУ НЕ ПОЗВОНИЛ И НИЧЕГО НЕ КУПИЛ", "ВОТ ЭТО НЕОЖИДАННЫЙ СЮЖЕТ"),
        ("СЕГОДНЯ ОБОШЛОСЬ БЕЗ ХЕРНИ", "КРАБ-БУХГАЛТЕР ОДОБРЯЕТ"),
        ("ПРОЖИЛ ВЕЧЕР БЕЗ ПРИКЛЮЧЕНИЙ", "МЕДАЛЬ ОТ МОРСКОЙ БУХГАЛТЕРИИ"),
    ),
}

ARCHETYPES = (
    "yellow_sponge", "nervous_squid", "star", "greedy_crab",
    "micro_plankton", "bubble_squirrel", "wary_fish", "sea_eel",
)
PLACES = ("night_room", "bank", "phone", "kitchen", "taxi", "hallway",
          "undersea_street", "office", "sea_diner", "bedroom", "beach", "bathroom")
EMOTIONS = ("suspicious", "panic", "tired", "confused", "angry", "smug", "sad", "dazed")
COLORS = (
    ("#121735", "#26446E"), ("#321C43", "#A25680"), ("#194448", "#A3C5AE"),
    ("#342239", "#D0816D"), ("#313E62", "#7CB8C5"), ("#49323C", "#E4B889"),
    ("#203C41", "#4B8587"), ("#3A2E64", "#B68EBD"),
)

@dataclass(frozen=True)
class Spec:
    user_id: int
    serial: int
    seed: int
    key: str
    substance: str
    risk: str
    archetype: str
    scene: str
    emotion: str
    top: str
    bottom: str


def init(connect):
    """Additive migration: user data and existing meme tables stay intact."""
    with connect() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS meme_dynamic_counter(
                user_id INTEGER PRIMARY KEY, serial INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS meme_dynamic_history(
                user_id INTEGER NOT NULL, serial INTEGER NOT NULL,
                meme_key TEXT NOT NULL, substance TEXT NOT NULL,
                risk TEXT NOT NULL, archetype TEXT NOT NULL, scene TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id,serial)
            );
            CREATE INDEX IF NOT EXISTS idx_meme_dynamic_history_user
              ON meme_dynamic_history(user_id,serial DESC);
        """)


def next_serial(connect, user_id):
    """SQLite IMMEDIATE transaction prevents concurrent sends reusing a serial."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("""INSERT INTO meme_dynamic_counter(user_id,serial) VALUES(?,1)
                 ON CONFLICT(user_id) DO UPDATE SET serial=serial+1""", (user_id,))
        serial = c.execute("SELECT serial FROM meme_dynamic_counter WHERE user_id=?",
                           (user_id,)).fetchone()[0]
    return int(serial)


def make_spec(substance, risk, uid, serial, seed=None):
    if risk not in RISK_TEXT:
        risk = "general"
    seed = secrets.randbits(64) if seed is None else int(seed)
    rng = random.Random(seed)
    archetype = ARCHETYPES[(serial + rng.randrange(len(ARCHETYPES))) % len(ARCHETYPES)]
    scene = PLACES[(serial * 3 + rng.randrange(len(PLACES))) % len(PLACES)]
    emotion = EMOTIONS[(serial + rng.randrange(len(EMOTIONS))) % len(EMOTIONS)]
    phrases = RISK_TEXT[risk]
    top, bottom = phrases[(serial + rng.randrange(len(phrases))) % len(phrases)]
    if substance in SUBS and rng.random() < .4:
        # Replaces a generic introductory line, but leaves the safety message.
        top = rng.choice((f"КОГДА ТЕБЯ НЕСЁТ ПОСЛЕ {SUBS[substance]}",
                          f"ПОСЛЕ {SUBS[substance]}: ЕСТЬ ИДЕЯ",
                          f"МОЗГ ПОД {SUBS[substance]} ПРЕДЛАГАЕТ ПЛАН"))
    key = hashlib.sha256(f"{uid}:{serial}:{seed}:{risk}".encode()).hexdigest()[:16]
    return Spec(uid, serial, seed, key, substance, risk,
                archetype, scene, emotion, top, bottom)


def _txt(draw, pos, message, size=26, color="white"):
    draw.text(pos, message, font=font(size), fill=color,
              stroke_width=2, stroke_fill="#121019")


def _backdrop(d, rng, scene, hue):
    first, second = hue
    d.rectangle((0, 0, SIZE, SIZE), fill=first)
    d.polygon(((0, 80), (850, 45), (850, 850), (0, 850)), fill=second)
    # Perspective floor gives each meme a real cartoon scene.
    d.polygon(((0, 590), (850, 560), (850, 850), (0, 850)), fill="#2D314A")
    d.line((0, 590, 850, 560), fill="#F6D3B8", width=9)
    if scene in ("night_room", "bedroom", "hallway", "office"):
        d.rounded_rectangle((38, 152, 255, 465), radius=15, fill="#182E55", outline="#F2CF9D", width=11)
        d.ellipse((111, 210, 171, 270), fill="#F9E7AF")
        for _ in range(12):
            x, y = rng.randrange(60, 235), rng.randrange(180, 447)
            d.ellipse((x, y, x+4, y+4), fill="#FDF7E7")
        if scene == "bedroom":
            d.rounded_rectangle((33, 640, 430, 750), radius=24, fill="#A9BDD9", outline="#34233F", width=10)
            d.rectangle((38, 718, 430, 770), fill="#EFD9C2")
    elif scene == "bank":
        d.rounded_rectangle((24, 197, 300, 485), radius=17, fill="#23394B", outline="#F3F6E3", width=8)
        _txt(d, (73, 244), "БАНК", 39, "#D5F5CF")
        _txt(d, (76, 340), "0.00", 42, "#F8D5D5")
    elif scene in ("phone", "taxi"):
        d.rounded_rectangle((38, 226, 230, 535), radius=18, fill="#111422", outline="#F9EDBD", width=9)
        d.rounded_rectangle((53, 259, 215, 483), radius=11, fill="#B5CAE1")
        _txt(d, (84, 306), "03:41", 26, "#11142C")
        d.ellipse((114, 420, 160, 466), fill="#D84B5C")
        if scene == "taxi":
            d.polygon(((15, 595), (145, 527), (250, 527), (325, 598)), fill="#F3D058")
    elif scene == "kitchen":
        d.rounded_rectangle((12, 194, 305, 420), radius=12, fill="#B9B7C9", outline="#3C344E", width=7)
        d.rectangle((25, 217, 280, 255), fill="#4A4C61")
        d.ellipse((70, 300, 163, 379), fill="#EFF0DC")
        d.arc((73, 302, 167, 386), -20, 120, fill="#484E60", width=9)
    elif scene == "undersea_street":
        for x in (30, 163, 265, 590, 745):
            h = rng.randrange(175, 325)
            d.rounded_rectangle((x, 560-h, x+75, 580), radius=19, fill="#45677C", outline="#D5E3D9", width=6)
            for sy in range(560-h+35, 555, 68):
                d.ellipse((x+20, sy, x+52, sy+35), fill="#FFCC81")
    elif scene == "sea_diner":
        d.rounded_rectangle((24, 210, 304, 492), radius=22, fill="#CC8560", outline="#FCEFC9", width=8)
        _txt(d, (67, 262), "24 / 7", 40)
        d.rectangle((20, 526, 355, 563), fill="#F1D5A0")
    elif scene == "bathroom":
        d.ellipse((38, 164, 300, 451), fill="#A5D3DA", outline="#E5EDE5", width=15)
        d.ellipse((74, 210, 256, 406), fill="#446A89")
        d.rectangle((23, 550, 300, 599), fill="#DFE9E7")
    elif scene == "beach":
        d.ellipse((55, 150, 229, 324), fill="#F9D99A")
        d.polygon(((0, 548), (850, 490), (850, 600), (0, 672)), fill="#87D8CF")
        for y in (557, 581, 606):
            d.arc((-110, y, 425, y+85), 0, 176, fill="#E1FAE6", width=6)
    for j in range(12):
        x = rng.randrange(0, SIZE)
        y = rng.randrange(170, 720)
        rad = rng.randrange(4, 27)
        d.ellipse((x-rad, y-rad, x+rad, y+rad), outline="#C4EFF1", width=2)


def _character(d, rng, actor, emotion, x=450, y=465):
    # Eight intentionally original sea-archetype silhouettes.
    tilt = rng.randrange(-28, 29)
    bob = rng.randrange(-28, 27)
    x, y = x + tilt, y + bob
    skin = "#E8D35B"
    if actor == "yellow_sponge":
        d.rounded_rectangle((x-185, y-175, x+190, y+206), radius=63,
                            fill="#E9C84C", outline="#333044", width=14)
        for _ in range(24):
            px, py = x+rng.randrange(-152,156), y+rng.randrange(-144,152)
            d.ellipse((px, py, px+10, py+12), fill="#D9A742")
        skin = "#E9C84C"
    elif actor == "nervous_squid":
        d.ellipse((x-165, y-217, x+167, y+70), fill="#79C9C1", outline="#233D54", width=14)
        d.polygon([(x-92,y+38),(x+80,y+38),(x+110,y+188),(x-115,y+188)],
                  fill="#A1D7C7", outline="#233D54")
        for tx in (-114,-45,41,113):
            d.line((x+tx, y+178, x+tx+rng.randrange(-28,29), y+255),
                   fill="#619F9E", width=17)
        skin = "#79C9C1"
    elif actor == "star":
        points=[]
        import math
        for i in range(10):
            angle = -math.pi/2 + i*math.pi/5
            rad = 220 if i%2==0 else 112
            points.append((x+rad*math.cos(angle), y+rad*math.sin(angle)))
        d.polygon(points, fill="#F2A4AF", outline="#7B5061", width=13)
        skin="#F2A4AF"
    elif actor == "greedy_crab":
        d.ellipse((x-188,y-128,x+180,y+186),fill="#D85D56",outline="#582A3C",width=14)
        for sign in (-1,1):
            d.line((x+sign*95,y+65,x+sign*232,y+160), fill="#A63D41",width=22)
            d.ellipse((x+sign*200-65,y+126,x+sign*200+65,y+219),
                      fill="#E17362",outline="#582A3C",width=9)
        skin="#D85D56"
    elif actor == "micro_plankton":
        d.ellipse((x-122,y-172,x+134,y+189),fill="#A7D861",outline="#385641",width=13)
        for sign in (-1,1):
            d.line((x+sign*42,y-165,x+sign*78,y-235),fill="#7FA642",width=13)
            d.ellipse((x+sign*78-16,y-246,x+sign*78+16,y-214),fill="#C0E686")
        skin="#A7D861"
    elif actor == "bubble_squirrel":
        d.ellipse((x-225,y-235,x+224,y+219),fill="#C6E6F3",outline="#F7F0DA",width=19)
        d.ellipse((x-125,y-150,x+125,y+149),fill="#BE9470",outline="#493A45",width=13)
        for sign in (-1,1):
            d.ellipse((x+sign*97-36,y-181,x+sign*97+36,y-108),
                      fill="#B38B70",outline="#493A45",width=8)
        skin="#BE9470"
    elif actor == "wary_fish":
        d.ellipse((x-205,y-158,x+205,y+160),fill="#82B3DA",outline="#2D4A66",width=15)
        d.polygon(((x+175,y-5),(x+280,y-144),(x+265,y+144)),fill="#568DB4")
        d.polygon(((x-35,y-149),(x+94,y-245),(x+112,y-129)),fill="#A8CCE1")
        skin="#82B3DA"
    else:
        d.ellipse((x-130,y-224,x+132,y+205),fill="#BC8AD0",outline="#4C365A",width=15)
        d.ellipse((x-75,y+150,x+65,y+274),fill="#965AAE",outline="#4C365A",width=13)
        skin="#BC8AD0"

    # Strong contrasting eyes, adjusted by emotion.
    eye_y=y-77 if actor != "nervous_squid" else y-93
    for ex in (-67,67):
        ry=16 if emotion=="tired" else 35 if emotion=="panic" else 26
        d.ellipse((x+ex-47,eye_y-ry,x+ex+47,eye_y+ry+16),
                  fill="#FFFFFF",outline="#3D314B",width=8)
        dx=18 if emotion=="suspicious" else rng.randrange(-17,18)
        d.ellipse((x+ex+dx-15,eye_y-4,x+ex+dx+17,eye_y+34),
                  fill="#172032")
        if emotion in ("panic","confused"):
            d.ellipse((x+ex+dx-9,eye_y-14,x+ex+dx+2,eye_y-2),
                      fill="#FFFFFF")
    if emotion == "angry":
        d.line((x-126,eye_y-66,x-22,eye_y-41),fill="#31263C",width=16)
        d.line((x+21,eye_y-41,x+125,eye_y-66),fill="#31263C",width=16)
    elif emotion in ("tired","sad"):
        d.arc((x-93,eye_y-83,x-23,eye_y-25),200,345,fill="#4B3850",width=11)
        d.arc((x+25,eye_y-83,x+96,eye_y-25),185,345,fill="#4B3850",width=11)
    if emotion in ("panic","confused","dazed"):
        d.ellipse((x-27,y+15,x+30,y+102),fill="#4B253B",outline="#F5A0A3",width=7)
    elif emotion in ("sad","tired"):
        d.arc((x-54,y+60,x+64,y+123),182,353,fill="#50354B",width=10)
    else:
        d.arc((x-65,y+9,x+73,y+93),0,180,fill="#3B2F45",width=12)
    # Dynamic arms and emotional prop.
    arm= rng.randrange(25,75)
    d.line([(x-139,y+104),(x-236,y+70-arm)],fill=skin,width=27)
    d.line([(x+138,y+111),(x+232,y+98+arm)],fill=skin,width=27)
    for hx,hy in ((x-236,y+70-arm),(x+232,y+98+arm)):
        d.ellipse((hx-25,hy-25,hx+25,hy+25),fill=skin,outline="#4F4251",width=5)
    return x,y


def _prop(d, rng, scene, risk, x, y):
    # Context objects ensure the joke is visible, not merely words over shapes.
    if risk in ("ex","contacts") or scene=="phone":
        d.rounded_rectangle((x,y-65,x+112,y+137),radius=18,
                            fill="#202137",outline="#EEE2D2",width=8)
        d.rounded_rectangle((x+13,y-40,x+99,y+90),radius=9,fill="#B7DDF3")
        d.ellipse((x+41,y+100,x+69,y+127),fill="#E95772")
    elif risk=="money" or scene=="bank":
        d.rounded_rectangle((x-18,y-28,x+138,y+95),radius=19,
                            fill="#715143",outline="#F1D195",width=8)
        _txt(d,(x+32,y+8),"0",58,"#F8F1DE")
    elif risk=="sleep":
        d.ellipse((x-5,y,x+135,y+60),fill="#EEEFE3",outline="#554E63",width=6)
        _txt(d,(x+23,y+10),"ZZ",31,"#695886")
    elif risk=="travel":
        d.rounded_rectangle((x-32,y-3,x+151,y+66),radius=19,
                            fill="#F7C75A",outline="#1B2438",width=8)
        for dx in (0,111):
            d.ellipse((x+dx-3,y+53,x+dx+37,y+102),fill="#1F2C3F")
    else:
        d.rounded_rectangle((x-24,y+7,x+149,y+71),radius=20,
                            fill="#FFF4D4",outline="#39405D",width=7)
        _txt(d,(x+21,y+18),"СТОП",31,"#6B3451")


def _wrap(d,text,ft,width):
    parts=text.split()
    out=[];line=""
    for word in parts:
        check=(line+" "+word).strip()
        if line and d.textbbox((0,0),check,font=ft)[2]>width:
            out.append(line);line=word
        else:line=check
    if line:out.append(line)
    return out


def _caption(d,text,top):
    lines=[]
    for size in range(50,23,-2):
        ft=font(size)
        lines=_wrap(d,text,ft,800)
        if len(lines)<=3:
            break
    h=size+11
    y=37 if top else SIZE-54-h*len(lines)
    for line in lines:
        d.text((SIZE//2+2,y+3),line,font=ft,anchor="mt",fill="#17141F",
               stroke_width=8,stroke_fill="#17141F")
        d.text((SIZE//2,y),line,font=ft,anchor="mt",fill="#FFFFFF",
               stroke_width=4,stroke_fill="#17141F")
        y+=h


def render(spec):
    """Render unique 850x850 JPEG bytes. Never uses external images."""
    rng=random.Random(spec.seed)
    palette=COLORS[(spec.serial+rng.randrange(len(COLORS)))%len(COLORS)]
    img=Image.new("RGB",(SIZE,SIZE),palette[0])
    d=ImageDraw.Draw(img)
    _backdrop(d,rng,spec.scene,palette)
    x,y=_character(d,rng,spec.archetype,spec.emotion)
    _prop(d,rng,spec.scene,spec.risk,x-289,505+rng.randrange(-38,35))
    # Old internet meme texture and distinct photo serial: even an unlucky
    # repeated composition has visibly different pixels and a unique key.
    for _ in range(45):
        px,py=rng.randrange(0,SIZE),rng.randrange(0,SIZE)
        d.line((px,py,px+rng.randrange(-13,14),py+rng.randrange(-13,14)),
               fill="#7C7793",width=1)
    d.rectangle((0,0,850,16),fill="#21162C")
    d.rectangle((0,829,850,850),fill="#21162C")
    d.text((20,834),f"НЕ НАТВОРИ ДЕЛОВ · #{spec.serial:06d}",
           font=font(13),fill="#E9D8BE")
    if spec.substance in SUBS:
        _txt(d,(19,171),SUBS[spec.substance],23,"#FFFFFF")
    _caption(d,spec.top,True)
    _caption(d,spec.bottom,False)
    out=io.BytesIO()
    img.save(out,"JPEG",quality=86,optimize=True)
    return out.getvalue()


def _record(connect,spec):
    created=datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as c:
        c.execute("""INSERT INTO meme_sends(user_id,meme_key,at)
                     VALUES(?,?,?)""",(spec.user_id,spec.key,created))
        c.execute("""INSERT INTO meme_dynamic_history
                     (user_id,serial,meme_key,substance,risk,archetype,scene,created_at)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (spec.user_id,spec.serial,spec.key,spec.substance,spec.risk,
                   spec.archetype,spec.scene,created))
        # Keep a recent window for ratings and feedback. Lifetime sequence is
        # never reset by this pruning, so old meme keys never get reused.
        c.execute("""DELETE FROM meme_dynamic_history WHERE user_id=?
                     AND serial NOT IN
                     (SELECT serial FROM meme_dynamic_history WHERE user_id=?
                      ORDER BY serial DESC LIMIT 300)""",
                  (spec.user_id,spec.user_id))
        c.execute("""DELETE FROM meme_sends WHERE user_id=?
                     AND id NOT IN
                     (SELECT id FROM meme_sends WHERE user_id=?
                      ORDER BY id DESC LIMIT 300)""",
                  (spec.user_id,spec.user_id))


def _vote_keyboard(key):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="😂 Зашло",callback_data=f"mv:1:{key}"),
         InlineKeyboardButton(text="🥱 Не смешно",callback_data=f"mv:-1:{key}")],
        [InlineKeyboardButton(text="🎲 Новый мем",callback_data="mnext")],
    ])


async def send(message, uid, substance, risk, connect, meme_key=None,
               record_meme=None, meme_buttons=None):
    """Send a new dynamically drawn photo; preserve the old handler signature."""
    serial=next_serial(connect,uid)
    spec=make_spec(substance,risk,uid,serial)
    data=await asyncio.to_thread(render,spec)
    await message.answer_photo(
        BufferedInputFile(data,filename=f"meme-{serial}.jpg"),
        caption="🎭 НЕ НАТВОРИ ДЕЛОВ · новый мем",
        reply_markup=_vote_keyboard(spec.key),
    )
    _record(connect,spec)
    return True


async def send_bot(bot,uid,substance,risk,connect,caption,reply_markup):
    """Scheduler uses the same generator, key uniqueness and shared history."""
    serial=next_serial(connect,uid)
    spec=make_spec(substance,risk,uid,serial)
    payload=await asyncio.to_thread(render,spec)
    await bot.send_photo(uid,BufferedInputFile(payload,filename=f"meme-{serial}.jpg"),
                         caption=caption[:1024],reply_markup=reply_markup)
    _record(connect,spec)
    return spec.key


if __name__=="__main__":
    import sys
    spec=make_spec("Мефедрон","ex",1,1,seed=2026)
    path=sys.argv[1] if len(sys.argv)>1 else "preview-meme.jpg"
    with open(path,"wb") as f:
        f.write(render(spec))
    print("Saved picture meme:",path)
