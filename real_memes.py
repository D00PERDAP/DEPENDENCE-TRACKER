"""Internet-backed old-school PHOTO meme creation.

- Free Imgflip /get_memes works without an API key.
- Optional approved local photo templates for genuinely Russian 2000s memes.
- Optional paid OpenAI Image API backgrounds only with explicit owner opt-in,
  bounded by an SQLite daily budget. Never sends personal user text to API.
- ALL Russian text is overlaid by Pillow with a known Cyrillic font.
- No auto-generated sea creature cartoons or fake text-only memes.

Imgflip templates are user-submitted and have varying rights. Use the site's API
and attribution where allowed; for commercial distribution curate/clear rights.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import random
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps, ImageFont, ImageFilter, ImageEnhance
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import meme_engine
import mellstroy_bank
import reaction_bank
import joke_catalog

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "meme_photo_cache"
CURATED = ROOT / "assets" / "memes" / "approved_photos"
SIZE = (850, 960)
GUARD = threading.RLock()
_LIST_TTL = 6 * 3600
_LIST_CACHE = {"time": 0.0, "items": []}
MAX_BYTES = 6_000_000

# Filter the public API's rotating catalog to recognizable, candid, mainly
# real-photo adult templates instead of cartoons, rage comics and text charts.
PHOTO_NAMES = {
    "drake hotline bling", "distracted boyfriend", "sad pablo escobar",
    "change my mind", "woman yelling at cat", "hide the pain harold",
    "grumpy cat", "ancient aliens", "roll safe think about it",
    "first world problems", "overly attached girlfriend", "bad luck brian",
    "y'all got any more of that", "third world skeptical kid",
    "leonardo dicaprio cheers", "the most interesting man in the world",
    "one does not simply", "laughing leo", "waiting skeleton",
    "disappointed black guy", "scared cat", "confused nick young",
    "surprised shaq", "awkward look monkey puppet", "confused math lady",
    "sweating jordan peele", "pablo escobar waiting",
    "leonardo dicaprio laughing"
}
# Some public templates are photographs of minors. Exclude them by default.
MINORS = {"bad luck brian", "third world skeptical kid", "side eyeing chloe"}

# Russian punchlines, edited by a human and SHORT enough to read instantly.
# No more random half-coherent word assembly or generated Russian in images.
from dark_memes import LINES, choose_lines


def init(connect):
    """Additional SQLite tables without touching existing user data."""
    joke_catalog.init(connect)
    with connect() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS real_meme_sent(
                user_id INTEGER NOT NULL, serial INTEGER NOT NULL,
                template_id TEXT NOT NULL, punchline TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(user_id,serial)
            );
            CREATE INDEX IF NOT EXISTS real_meme_sent_idx
              ON real_meme_sent(user_id, serial DESC);
            CREATE TABLE IF NOT EXISTS real_meme_ai_budget(
                day TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS real_meme_search_budget(
                day TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS real_meme_search_cache(
                query TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, results TEXT NOT NULL
            );
        """)

def _json_url(url, data=None, token=None, timeout=7):
    payload=None if data is None else urllib.parse.urlencode(data).encode("utf-8")
    headers={"User-Agent":"NoTroubleMemeBot/4.0 (+meme-photo-client)"}
    if token:
        headers["Authorization"]="Bearer "+token
    request=urllib.request.Request(url,data=payload,headers=headers)
    with urllib.request.urlopen(request,timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"Image provider HTTP {response.status}")
        contents=response.read(2_000_001)
        if len(contents)>2_000_000:
            raise ValueError("Meme API response too large")
    return json.loads(contents)

def _top_templates():
    global _LIST_CACHE
    now=time.monotonic()
    with GUARD:
        if _LIST_CACHE["items"] and now-_LIST_CACHE["time"]<_LIST_TTL:
            return _LIST_CACHE["items"]
    response=_json_url("https://api.imgflip.com/get_memes",timeout=8)
    if not response.get("success"):
        raise RuntimeError("Imgflip API returned error")
    found=[]
    for row in response.get("data",{}).get("memes",[]):
        name=str(row.get("name","")).lower().strip()
        ident=str(row.get("id",""))
        link=str(row.get("url",""))
        if (name not in PHOTO_NAMES or name in MINORS
            or not ident.isdigit()
            or not re.fullmatch(r"https://i\.imgflip\.com/[a-zA-Z0-9]+\.(?:jpg|jpeg|png|webp)",link)
            or int(row.get("width") or 0)<280
            or int(row.get("height") or 0)<250):
            continue
        found.append({"id":ident,"name":row["name"],"url":link})
    if not found:
        raise RuntimeError("No approved photomeme templates in Imgflip catalog")
    with GUARD:
        _LIST_CACHE={"time":time.monotonic(),"items":found}
    return found

def _premium_templates(connect):
    """Optional Premium photo-template search. Owner must opt in explicitly.

    At most two paid search requests per UTC day, even across restarts. Daily
    results are cached in SQLite. NSFW search deliberately remains disabled.
    """
    key=os.getenv("IMGFLIP_API_KEY","").strip()
    if (not key or os.getenv("MEME_PREMIUM_SEARCH","0")!="1"):
        return []
    now=datetime.now(timezone.utc)
    day=now.strftime("%Y-%m-%d")
    query_terms=[x.strip() for x in os.getenv(
        "MEME_PREMIUM_TERMS","пошалим;русский мем").split(";") if x.strip()][:2]
    output=[]
    for query in query_terms:
        with connect() as c:
            prev=c.execute("SELECT fetched_at,results FROM real_meme_search_cache"
                           " WHERE query=?",(query,)).fetchone()
        if prev:
            try:
                age=(now-datetime.fromisoformat(prev["fetched_at"])).total_seconds()
                if age<86400:
                    output.extend(json.loads(prev["results"]))
                    continue
            except (ValueError,TypeError,KeyError):
                pass
        with connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT attempts FROM real_meme_search_budget WHERE day=?",
                          (day,)).fetchone()
            if row and row["attempts"]>=2:
                continue
            c.execute("""INSERT INTO real_meme_search_budget(day,attempts) VALUES(?,1)
                       ON CONFLICT(day) DO UPDATE SET attempts=attempts+1""",(day,))
        try:
            answer=_json_url("https://api.imgflip.com/search_memes",
                             data={"query":query,"type":"image","include_nsfw":"0"},
                             token=key,timeout=10)
            if not answer.get("success"):
                raise RuntimeError("Imgflip Premium search error")
            approved=[]
            for item in answer.get("data",{}).get("memes",[])[:80]:
                ident=str(item.get("id",""))
                url=str(item.get("url",""))
                name=str(item.get("name","")).lower()
                if (not ident.isdigit() or
                    not re.fullmatch(r"https://i\.imgflip\.com/[a-zA-Z0-9]+\.(?:jpg|jpeg|png|webp)",url)
                    or any(x in name for x in ("kid","child","teen","baby","little girl","young boy"))
                    or int(item.get("width") or 0)<280
                    or int(item.get("height") or 0)<250):
                    continue
                approved.append({"id":ident,"url":url,"name":str(item.get("name",""))})
            with connect() as c:
                c.execute("""INSERT INTO real_meme_search_cache(query,fetched_at,results)
                             VALUES(?,?,?) ON CONFLICT(query)
                             DO UPDATE SET fetched_at=excluded.fetched_at,
                                           results=excluded.results""",
                          (query,now.isoformat(timespec="seconds"),json.dumps(approved,ensure_ascii=False)))
            output.extend(approved)
        except Exception as exc:
            print(f"Imgflip Premium search failed (query omitted): "
                  f"{type(exc).__name__}: {exc}",flush=True)
    return output


def _curated_templates():
    # A photo submitted by the Telegram admin with /addmeme ... YES is both
    # EXIF-cleaned and stored with rights provenance. Prefer this curated source
    # over scraped random content. Keep its substance/risk tags for matching.
    output=[]
    assets=ROOT/"assets"/"memes"
    ledger=ROOT/"data"/"meme_admin_imports.json"
    if ledger.is_file():
        try:
            rows=json.loads(ledger.read_text(encoding="utf-8"))
            if not isinstance(rows,list):
                rows=[]
            for item in rows:
                if not isinstance(item,dict) or item.get("rights_confirmed_by_admin") is not True:
                    continue
                path=(assets/str(item.get("file",""))).resolve()
                if not path.is_relative_to(assets.resolve()):
                    continue
                if path.is_file() and path.suffix.lower() in (".jpeg",".jpg",".png",".webp") and 1200<path.stat().st_size<=MAX_BYTES:
                    output.append({"id":"approved-"+str(item.get("sha256",""))[:16],
                                   "name":"approved Russian retro template",
                                   "path":path,"substance":item.get("substance","general"),
                                   "risk":item.get("risk","general")})
        except (OSError,ValueError,TypeError) as exc:
            print(f"Photo approval ledger unavailable: {type(exc).__name__}",flush=True)
    if CURATED.is_dir():
        for p in CURATED.iterdir():
            if p.is_file() and p.suffix.lower() in (".jpeg",".jpg",".png",".webp") and 1200<p.stat().st_size<=MAX_BYTES:
                output.append({"id":"owner-"+hashlib.sha1(p.name.encode()).hexdigest()[:12],
                               "name":"approved Russian retro template",
                               "path":p,"substance":"general","risk":"general"})
    return output

def _source_bytes(template):
    if "path" in template:
        return template["path"].read_bytes()
    ident=str(template["id"])
    if not ident.isdigit():
        raise ValueError("Invalid imgflip ID")
    try:
        CACHE.mkdir(parents=True,exist_ok=True)
    except OSError:
        pass
    cached=CACHE/(ident+".jpg")
    if cached.is_file() and 1000<cached.stat().st_size<=MAX_BYTES:
        return cached.read_bytes()
    link=template["url"]
    if not re.fullmatch(r"https://i\.imgflip\.com/[a-zA-Z0-9]+\.(?:jpg|jpeg|png|webp)",link):
        raise ValueError("Unexpected template host")
    req=urllib.request.Request(link,headers={"User-Agent":"NoTroubleMemeBot/4.0"})
    with urllib.request.urlopen(req,timeout=5) as response:
        if response.status!=200 or not response.headers.get("Content-Type","").startswith("image/"):
            raise ValueError("Invalid photo source")
        payload=response.read(MAX_BYTES+1)
    if len(payload)>MAX_BYTES or len(payload)<1000:
        raise ValueError("Photo template size invalid")
    with Image.open(io.BytesIO(payload)) as opened:
        opened.verify()
    try:
        cached.write_bytes(payload)
    except OSError:
        pass # read-only disk: photo still renders from memory
    return payload

def _fonts():
    return (
        # DejaVu supports Ё/Й/Ъ/Ь reliably; Impact may lack Cyrillic glyphs.
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    )

def _font(size):
    for name in _fonts():
        try: return ImageFont.truetype(name,size)
        except OSError: pass
    raise RuntimeError("Cyrillic font unavailable; install fonts-dejavu-core")

def _wrap(draw,text,ft,width):
    lines=[]
    words=text.split()
    current=""
    for word in words:
        sample=(current+" "+word).strip()
        if current and draw.textbbox((0,0),sample,font=ft)[2]>width:
            lines.append(current);current=word
        else:
            current=sample
    if current: lines.append(current)
    return lines

def _legend(draw,text,bottom=False):
    text=text.upper().strip()
    if not text:
        return
    for size in range(64,21,-2):
        ft=_font(size)
        lines=_wrap(draw,text,ft,SIZE[0]-54)
        if len(lines)<=3:
            break
    step=size+14
    y=(SIZE[1]-48-step*len(lines)) if bottom else 30
    for line in lines:
        draw.text((SIZE[0]//2+2,y+3),line,
                  fill="#0C0A10",font=ft,anchor="mt",
                  stroke_width=9,stroke_fill="#08080A")
        draw.text((SIZE[0]//2,y),line,fill="white",
                  font=ft,anchor="mt",stroke_width=5,
                  stroke_fill="#08080A")
        y+=step

def compose(photo_bytes,top,bottom,serial,source_label="Imgflip"):
    """Authentic image with verified Cyrillic captions, not AI-drawn letters."""
    with Image.open(io.BytesIO(photo_bytes)) as picture:
        picture.load()
        if picture.width<240 or picture.height<240 or picture.width*picture.height>26_000_000:
            raise ValueError("Template photo dimensions unsupported")
        base=ImageOps.exif_transpose(picture).convert("RGB")
    # Preserve the awkward framing of the original; no aggressive square cropping.
    contained=ImageOps.contain(base,(SIZE[0],SIZE[1]-24),Image.Resampling.LANCZOS)
    cover=ImageOps.fit(base,SIZE,Image.Resampling.LANCZOS)
    cover=cover.filter(ImageFilter.GaussianBlur(15))
    cover=ImageEnhance.Brightness(cover).enhance(.65)
    x=(SIZE[0]-contained.width)//2
    y=(SIZE[1]-contained.height)//2
    cover.paste(contained,(x,y))
    overlay=Image.new("RGBA",SIZE,(0,0,0,0))
    layer=ImageDraw.Draw(overlay)
    # Transparent shadows, not 90% black poster cards; original image stays visible.
    for dy in range(165):
        alpha=int((1-dy/165)**2*110)
        layer.rectangle((0,dy,SIZE[0],dy+1),fill=(0,0,0,alpha))
        layer.rectangle((0,SIZE[1]-1-dy,SIZE[0],SIZE[1]-dy),
                        fill=(0,0,0,alpha))
    cover=Image.alpha_composite(cover.convert("RGBA"),overlay).convert("RGB")
    draw=ImageDraw.Draw(cover)
    _legend(draw,top)
    _legend(draw,bottom,bottom=True)
    # Attribution without ruining the layout. The unique serial is *not*
    # artificially printed on the picture; unique text-photo combinations do it.
    credit=f"{source_label} · #{serial}"
    draw.text((13,SIZE[1]-15),credit,font=_font(11),fill="#EEE7E0",
              stroke_width=2,stroke_fill="#202027")
    buf=io.BytesIO()
    cover.save(buf,"JPEG",quality=79,optimize=True)
    return buf.getvalue()

def _lines(risk, serial, substance=""):
    """Original Russian dark drug humor, with profane substance-specific rotation."""
    return choose_lines(risk, serial, substance)

def _budget(connect):
    """Off by default; charged API usage requires explicit owner's opt-in."""
    if os.getenv("MEME_AI_ENABLED","0")!="1" or not os.getenv("OPENAI_API_KEY"):
        return False
    try:
        cap=max(0,min(20,int(os.getenv("MEME_AI_DAILY_LIMIT","2"))))
    except ValueError:
        cap=0
    if not cap:
        return False
    today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT attempts FROM real_meme_ai_budget WHERE day=?",
                      (today,)).fetchone()
        used=int(row[0]) if row else 0
        if used>=cap:
            return False
        c.execute("""INSERT INTO real_meme_ai_budget(day,attempts) VALUES(?,1)
                  ON CONFLICT(day) DO UPDATE SET attempts=attempts+1""",(today,))
    return True

def _ai_image(risk,seed):
    """Paid OpenAI image API; produce only an uncaptioned fictional *adult* photo."""
    topics={
        "ex":"person sitting with old mobile phone looking embarrassed",
        "money":"person checking wallet in disbelief",
        "more":"person about to make a silly late-night decision, instantly regretting it",
        "sleep":"person exhausted in front of old CRT monitor at 4am",
        "travel":"adult looking at car keys and deciding to stay home",
        "contacts":"person hovering over an old mobile phone with doubtful expression",
        "paranoia":"person staring suspiciously at completely ordinary curtains",
        "general":"person awkwardly posing in an ordinary apartment",
    }
    prompt=(
        "Make an ORIGINAL fictional photorealistic awkward Russian-internet "
        "meme photo, like a poorly-composed 2005-2011 cheap compact-camera shot. "
        "Funny accidental candid moment, no polished studio lighting, "
        "flash glare, compression noise, ordinary cluttered home, small odd "
        "visual detail, slightly absurd expression. All depicted humans "
        "are clearly ADULTS 25+, no real person lookalikes. Situation: "
        +topics.get(risk,"adult awkwardly posing in apartment")+
        ". No drug consumption or paraphernalia, no text, no labels, "
        "no watermarks, no logos. The text caption will be drawn later."
    )
    data=json.dumps({"model":os.getenv("MEME_IMAGE_MODEL","gpt-image-2.5-flare"),
                     "prompt":prompt,"size":"1024x1024",
                     "quality":"low","output_format":"jpeg"}).encode()
    request=urllib.request.Request("https://api.openai.com/v1/images/generations",
                  data=data,headers={"Content-Type":"application/json",
                                     "Authorization":"Bearer "+os.environ["OPENAI_API_KEY"]},
                  method="POST")
    with urllib.request.urlopen(request,timeout=90) as result:
        answer=json.loads(result.read(12_000_000))
    return base64.b64decode(answer["data"][0]["b64_json"])

def _prepare(connect, uid, substance, risk, allow_paid=False):
    """Try all available REAL photo sources, even when a local bank is broken.

    One corrupt local image, an unreadable optional reaction SQLite database,
    or a stale catalog must not make the whole Telegram meme button unusable.
    Priority: multi-person photos, Mellstroy frames, owner photos, Imgflip.
    Only buy AI imagery when the owner explicitly enabled its API budget.
    """
    serial = meme_engine.next_serial(connect, uid)
    risk = risk if risk in LINES else "general"
    joke = joke_catalog.select(connect, uid, risk, serial)
    captions = ["МЕФЕДРОН УВОЛЕН", "МЕФЕДРОН ОБИДЕЛСЯ", "МЕФЕДРОН ПРОПАЛ", "МЕФЕДРОН ЗАДУМАЛСЯ", "МЕФЕДРОН ЗАБЛУДИЛСЯ", "МЕФЕДРОН ОПОЗДАЛ", "МЕФЕДРОН НЕ ВЫВОЗИТ", "МЕФЕДРОН УСТАЛ", "МЕФЕДРОН ПЛАЧЕТ", "МЕФЕДРОН НЕ ОДОБРЯЕТ", "МЕФЕДРОН В ДОЛГАХ", "МЕФЕДРОН НА БОЛЬНИЧНОМ", "МЕФЕДРОН В ОТПУСКЕ", "МЕФЕДРОН НА ПЕРЕСДАЧЕ", "МЕФЕДРОН В ПРОБКЕ", "МЕФЕДРОН НА СОБЕСЕДОВАНИИ", "МЕФЕДРОН В ДЕКРЕТЕ", "МЕФЕДРОН НА СОВЕЩАНИИ", "МЕФЕДРОН В ЧАТЕ", "МЕФЕДРОН НЕ ОТВЕЧАЕТ", "АЛЬФА УСТАЛ", "АЛЬФА ПЛАЧЕТ", "АЛЬФА НЕ ОДОБРЯЕТ", "АЛЬФА В ДОЛГАХ", "АЛЬФА НА БОЛЬНИЧНОМ", "АЛЬФА В ОТПУСКЕ", "АЛЬФА НА ПЕРЕСДАЧЕ", "АЛЬФА В ПРОБКЕ", "АЛЬФА НА СОБЕСЕДОВАНИИ", "АЛЬФА В ДЕКРЕТЕ", "АЛЬФА НА СОВЕЩАНИИ", "АЛЬФА В ЧАТЕ", "АЛЬФА НЕ ОТВЕЧАЕТ", "АЛЬФА СДАЁТ ЕГЭ", "АЛЬФА ПОТЕРЯЛ ПАСПОРТ", "АЛЬФА ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "АЛЬФА ПРОСИТ ОТПУСК", "АЛЬФА ВЫЗВАЛ ТАКСИ", "АЛЬФА ЗАБЫЛ ПАРОЛЬ", "АЛЬФА ВЫШЕЛ ИЗ ЧАТА", "МЕФ В ПРОБКЕ", "МЕФ НА СОБЕСЕДОВАНИИ", "МЕФ В ДЕКРЕТЕ", "МЕФ НА СОВЕЩАНИИ", "МЕФ В ЧАТЕ", "МЕФ НЕ ОТВЕЧАЕТ", "МЕФ СДАЁТ ЕГЭ", "МЕФ ПОТЕРЯЛ ПАСПОРТ", "МЕФ ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "МЕФ ПРОСИТ ОТПУСК", "МЕФ ВЫЗВАЛ ТАКСИ", "МЕФ ЗАБЫЛ ПАРОЛЬ", "МЕФ ВЫШЕЛ ИЗ ЧАТА", "МЕФ ОТКРЫЛ ИП", "МЕФ ЗАСТРЯЛ В ЛИФТЕ", "МЕФ КУПИЛ ГАРАЖ", "МЕФ ИЩЕТ СМЫСЛ", "МЕФ ЗАПИСАЛСЯ К ВРАЧУ", "МЕФ ВЫЗВАЛ САНТЕХНИКА", "МЕФ ПОЛУЧИЛ ДВОЙКУ", "СОЛЬ ПОТЕРЯЛ ПАСПОРТ", "СОЛЬ ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "СОЛЬ ПРОСИТ ОТПУСК", "СОЛЬ ВЫЗВАЛ ТАКСИ", "СОЛЬ ЗАБЫЛ ПАРОЛЬ", "СОЛЬ ВЫШЕЛ ИЗ ЧАТА", "СОЛЬ ОТКРЫЛ ИП", "СОЛЬ ЗАСТРЯЛ В ЛИФТЕ", "СОЛЬ КУПИЛ ГАРАЖ", "СОЛЬ ИЩЕТ СМЫСЛ", "СОЛЬ ЗАПИСАЛСЯ К ВРАЧУ", "СОЛЬ ВЫЗВАЛ САНТЕХНИКА", "СОЛЬ ПОЛУЧИЛ ДВОЙКУ", "СОЛЬ ПОТЕРЯЛ ТАПОК", "СОЛЬ СПОРИТ С ЧАЙНИКОМ", "СОЛЬ ПРОВАЛИЛ ЭКЗАМЕН", "СОЛЬ ОТМЕНИЛ ПОНЕДЕЛЬНИК", "СОЛЬ ВЫЗВАЛ ДИРЕКТОРА", "СОЛЬ ОПЯТЬ НА СВЯЗИ", "СОЛЬ УВОЛЕН", "СКОРОСТЬ ЗАСТРЯЛ В ЛИФТЕ", "СКОРОСТЬ КУПИЛ ГАРАЖ", "СКОРОСТЬ ИЩЕТ СМЫСЛ", "СКОРОСТЬ ЗАПИСАЛСЯ К ВРАЧУ", "СКОРОСТЬ ВЫЗВАЛ САНТЕХНИКА", "СКОРОСТЬ ПОЛУЧИЛ ДВОЙКУ", "СКОРОСТЬ ПОТЕРЯЛ ТАПОК", "СКОРОСТЬ СПОРИТ С ЧАЙНИКОМ", "СКОРОСТЬ ПРОВАЛИЛ ЭКЗАМЕН", "СКОРОСТЬ ОТМЕНИЛ ПОНЕДЕЛЬНИК", "СКОРОСТЬ ВЫЗВАЛ ДИРЕКТОРА", "СКОРОСТЬ ОПЯТЬ НА СВЯЗИ", "СКОРОСТЬ УВОЛЕН", "СКОРОСТЬ ОБИДЕЛСЯ", "СКОРОСТЬ ПРОПАЛ", "СКОРОСТЬ ЗАДУМАЛСЯ", "СКОРОСТЬ ЗАБЛУДИЛСЯ", "СКОРОСТЬ ОПОЗДАЛ", "СКОРОСТЬ НЕ ВЫВОЗИТ", "СКОРОСТЬ УСТАЛ", "АМФЕТАМИН СПОРИТ С ЧАЙНИКОМ", "АМФЕТАМИН ПРОВАЛИЛ ЭКЗАМЕН", "АМФЕТАМИН ОТМЕНИЛ ПОНЕДЕЛЬНИК", "АМФЕТАМИН ВЫЗВАЛ ДИРЕКТОРА", "АМФЕТАМИН ОПЯТЬ НА СВЯЗИ", "АМФЕТАМИН УВОЛЕН", "АМФЕТАМИН ОБИДЕЛСЯ", "АМФЕТАМИН ПРОПАЛ", "АМФЕТАМИН ЗАДУМАЛСЯ", "АМФЕТАМИН ЗАБЛУДИЛСЯ", "АМФЕТАМИН ОПОЗДАЛ", "АМФЕТАМИН НЕ ВЫВОЗИТ", "АМФЕТАМИН УСТАЛ", "АМФЕТАМИН ПЛАЧЕТ", "АМФЕТАМИН НЕ ОДОБРЯЕТ", "АМФЕТАМИН В ДОЛГАХ", "АМФЕТАМИН НА БОЛЬНИЧНОМ", "АМФЕТАМИН В ОТПУСКЕ", "АМФЕТАМИН НА ПЕРЕСДАЧЕ", "АМФЕТАМИН В ПРОБКЕ", "КЕТАМИН ПРОПАЛ", "КЕТАМИН ЗАДУМАЛСЯ", "КЕТАМИН ЗАБЛУДИЛСЯ", "КЕТАМИН ОПОЗДАЛ", "КЕТАМИН НЕ ВЫВОЗИТ", "КЕТАМИН УСТАЛ", "КЕТАМИН ПЛАЧЕТ", "КЕТАМИН НЕ ОДОБРЯЕТ", "КЕТАМИН В ДОЛГАХ", "КЕТАМИН НА БОЛЬНИЧНОМ", "КЕТАМИН В ОТПУСКЕ", "КЕТАМИН НА ПЕРЕСДАЧЕ", "КЕТАМИН В ПРОБКЕ", "КЕТАМИН НА СОБЕСЕДОВАНИИ", "КЕТАМИН В ДЕКРЕТЕ", "КЕТАМИН НА СОВЕЩАНИИ", "КЕТАМИН В ЧАТЕ", "КЕТАМИН НЕ ОТВЕЧАЕТ", "КЕТАМИН СДАЁТ ЕГЭ", "КЕТАМИН ПОТЕРЯЛ ПАСПОРТ", "КОКАИН НЕ ОДОБРЯЕТ", "КОКАИН В ДОЛГАХ", "КОКАИН НА БОЛЬНИЧНОМ", "КОКАИН В ОТПУСКЕ", "КОКАИН НА ПЕРЕСДАЧЕ", "КОКАИН В ПРОБКЕ", "КОКАИН НА СОБЕСЕДОВАНИИ", "КОКАИН В ДЕКРЕТЕ", "КОКАИН НА СОВЕЩАНИИ", "КОКАИН В ЧАТЕ", "КОКАИН НЕ ОТВЕЧАЕТ", "КОКАИН СДАЁТ ЕГЭ", "КОКАИН ПОТЕРЯЛ ПАСПОРТ", "КОКАИН ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "КОКАИН ПРОСИТ ОТПУСК", "КОКАИН ВЫЗВАЛ ТАКСИ", "КОКАИН ЗАБЫЛ ПАРОЛЬ", "КОКАИН ВЫШЕЛ ИЗ ЧАТА", "КОКАИН ОТКРЫЛ ИП", "КОКАИН ЗАСТРЯЛ В ЛИФТЕ", "МДМА В ДЕКРЕТЕ", "МДМА НА СОВЕЩАНИИ", "МДМА В ЧАТЕ", "МДМА НЕ ОТВЕЧАЕТ", "МДМА СДАЁТ ЕГЭ", "МДМА ПОТЕРЯЛ ПАСПОРТ", "МДМА ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "МДМА ПРОСИТ ОТПУСК", "МДМА ВЫЗВАЛ ТАКСИ", "МДМА ЗАБЫЛ ПАРОЛЬ", "МДМА ВЫШЕЛ ИЗ ЧАТА", "МДМА ОТКРЫЛ ИП", "МДМА ЗАСТРЯЛ В ЛИФТЕ", "МДМА КУПИЛ ГАРАЖ", "МДМА ИЩЕТ СМЫСЛ", "МДМА ЗАПИСАЛСЯ К ВРАЧУ", "МДМА ВЫЗВАЛ САНТЕХНИКА", "МДМА ПОЛУЧИЛ ДВОЙКУ", "МДМА ПОТЕРЯЛ ТАПОК", "МДМА СПОРИТ С ЧАЙНИКОМ", "ЛСД ПРОСИТ ОТПУСК", "ЛСД ВЫЗВАЛ ТАКСИ", "ЛСД ЗАБЫЛ ПАРОЛЬ", "ЛСД ВЫШЕЛ ИЗ ЧАТА", "ЛСД ОТКРЫЛ ИП", "ЛСД ЗАСТРЯЛ В ЛИФТЕ", "ЛСД КУПИЛ ГАРАЖ", "ЛСД ИЩЕТ СМЫСЛ", "ЛСД ЗАПИСАЛСЯ К ВРАЧУ", "ЛСД ВЫЗВАЛ САНТЕХНИКА", "ЛСД ПОЛУЧИЛ ДВОЙКУ", "ЛСД ПОТЕРЯЛ ТАПОК", "ЛСД СПОРИТ С ЧАЙНИКОМ", "ЛСД ПРОВАЛИЛ ЭКЗАМЕН", "ЛСД ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ЛСД ВЫЗВАЛ ДИРЕКТОРА", "ЛСД ОПЯТЬ НА СВЯЗИ", "ЛСД УВОЛЕН", "ЛСД ОБИДЕЛСЯ", "ЛСД ПРОПАЛ", "ЗИП ИЩЕТ СМЫСЛ", "ЗИП ЗАПИСАЛСЯ К ВРАЧУ", "ЗИП ВЫЗВАЛ САНТЕХНИКА", "ЗИП ПОЛУЧИЛ ДВОЙКУ", "ЗИП ПОТЕРЯЛ ТАПОК", "ЗИП СПОРИТ С ЧАЙНИКОМ", "ЗИП ПРОВАЛИЛ ЭКЗАМЕН", "ЗИП ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ЗИП ВЫЗВАЛ ДИРЕКТОРА", "ЗИП ОПЯТЬ НА СВЯЗИ", "ЗИП УВОЛЕН", "ЗИП ОБИДЕЛСЯ", "ЗИП ПРОПАЛ", "ЗИП ЗАДУМАЛСЯ", "ЗИП ЗАБЛУДИЛСЯ", "ЗИП ОПОЗДАЛ", "ЗИП НЕ ВЫВОЗИТ", "ЗИП УСТАЛ", "ЗИП ПЛАЧЕТ", "ЗИП НЕ ОДОБРЯЕТ", "ПАКЕТ ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ПАКЕТ ВЫЗВАЛ ДИРЕКТОРА", "ПАКЕТ ОПЯТЬ НА СВЯЗИ", "ПАКЕТ УВОЛЕН", "ПАКЕТ ОБИДЕЛСЯ", "ПАКЕТ ПРОПАЛ", "ПАКЕТ ЗАДУМАЛСЯ", "ПАКЕТ ЗАБЛУДИЛСЯ", "ПАКЕТ ОПОЗДАЛ", "ПАКЕТ НЕ ВЫВОЗИТ", "ПАКЕТ УСТАЛ", "ПАКЕТ ПЛАЧЕТ", "ПАКЕТ НЕ ОДОБРЯЕТ", "ПАКЕТ В ДОЛГАХ", "ПАКЕТ НА БОЛЬНИЧНОМ", "ПАКЕТ В ОТПУСКЕ", "ПАКЕТ НА ПЕРЕСДАЧЕ", "ПАКЕТ В ПРОБКЕ", "ПАКЕТ НА СОБЕСЕДОВАНИИ", "ПАКЕТ В ДЕКРЕТЕ", "КЛАД ЗАБЛУДИЛСЯ", "КЛАД ОПОЗДАЛ", "КЛАД НЕ ВЫВОЗИТ", "КЛАД УСТАЛ", "КЛАД ПЛАЧЕТ", "КЛАД НЕ ОДОБРЯЕТ", "КЛАД В ДОЛГАХ", "КЛАД НА БОЛЬНИЧНОМ", "КЛАД В ОТПУСКЕ", "КЛАД НА ПЕРЕСДАЧЕ", "КЛАД В ПРОБКЕ", "КЛАД НА СОБЕСЕДОВАНИИ", "КЛАД В ДЕКРЕТЕ", "КЛАД НА СОВЕЩАНИИ", "КЛАД В ЧАТЕ", "КЛАД НЕ ОТВЕЧАЕТ", "КЛАД СДАЁТ ЕГЭ", "КЛАД ПОТЕРЯЛ ПАСПОРТ", "КЛАД ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "КЛАД ПРОСИТ ОТПУСК", "ТАЙНИК НА БОЛЬНИЧНОМ", "ТАЙНИК В ОТПУСКЕ", "ТАЙНИК НА ПЕРЕСДАЧЕ", "ТАЙНИК В ПРОБКЕ", "ТАЙНИК НА СОБЕСЕДОВАНИИ", "ТАЙНИК В ДЕКРЕТЕ", "ТАЙНИК НА СОВЕЩАНИИ", "ТАЙНИК В ЧАТЕ", "ТАЙНИК НЕ ОТВЕЧАЕТ", "ТАЙНИК СДАЁТ ЕГЭ", "ТАЙНИК ПОТЕРЯЛ ПАСПОРТ", "ТАЙНИК ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "ТАЙНИК ПРОСИТ ОТПУСК", "ТАЙНИК ВЫЗВАЛ ТАКСИ", "ТАЙНИК ЗАБЫЛ ПАРОЛЬ", "ТАЙНИК ВЫШЕЛ ИЗ ЧАТА", "ТАЙНИК ОТКРЫЛ ИП", "ТАЙНИК ЗАСТРЯЛ В ЛИФТЕ", "ТАЙНИК КУПИЛ ГАРАЖ", "ТАЙНИК ИЩЕТ СМЫСЛ", "БОБЁР В ЧАТЕ", "БОБЁР НЕ ОТВЕЧАЕТ", "БОБЁР СДАЁТ ЕГЭ", "БОБЁР ПОТЕРЯЛ ПАСПОРТ", "БОБЁР ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "БОБЁР ПРОСИТ ОТПУСК", "БОБЁР ВЫЗВАЛ ТАКСИ", "БОБЁР ЗАБЫЛ ПАРОЛЬ", "БОБЁР ВЫШЕЛ ИЗ ЧАТА", "БОБЁР ОТКРЫЛ ИП", "БОБЁР ЗАСТРЯЛ В ЛИФТЕ", "БОБЁР КУПИЛ ГАРАЖ", "БОБЁР ИЩЕТ СМЫСЛ", "БОБЁР ЗАПИСАЛСЯ К ВРАЧУ", "БОБЁР ВЫЗВАЛ САНТЕХНИКА", "БОБЁР ПОЛУЧИЛ ДВОЙКУ", "БОБЁР ПОТЕРЯЛ ТАПОК", "БОБЁР СПОРИТ С ЧАЙНИКОМ", "БОБЁР ПРОВАЛИЛ ЭКЗАМЕН", "БОБЁР ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ДУРШЛАГ ЗАБЫЛ ПАРОЛЬ", "ДУРШЛАГ ВЫШЕЛ ИЗ ЧАТА", "ДУРШЛАГ ОТКРЫЛ ИП", "ДУРШЛАГ ЗАСТРЯЛ В ЛИФТЕ", "ДУРШЛАГ КУПИЛ ГАРАЖ", "ДУРШЛАГ ИЩЕТ СМЫСЛ", "ДУРШЛАГ ЗАПИСАЛСЯ К ВРАЧУ", "ДУРШЛАГ ВЫЗВАЛ САНТЕХНИКА", "ДУРШЛАГ ПОЛУЧИЛ ДВОЙКУ", "ДУРШЛАГ ПОТЕРЯЛ ТАПОК", "ДУРШЛАГ СПОРИТ С ЧАЙНИКОМ", "ДУРШЛАГ ПРОВАЛИЛ ЭКЗАМЕН", "ДУРШЛАГ ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ДУРШЛАГ ВЫЗВАЛ ДИРЕКТОРА", "ДУРШЛАГ ОПЯТЬ НА СВЯЗИ", "ДУРШЛАГ УВОЛЕН", "ДУРШЛАГ ОБИДЕЛСЯ", "ДУРШЛАГ ПРОПАЛ", "ДУРШЛАГ ЗАДУМАЛСЯ", "ДУРШЛАГ ЗАБЛУДИЛСЯ", "ПЕЛЬМЕНЬ ВЫЗВАЛ САНТЕХНИКА", "ПЕЛЬМЕНЬ ПОЛУЧИЛ ДВОЙКУ", "ПЕЛЬМЕНЬ ПОТЕРЯЛ ТАПОК", "ПЕЛЬМЕНЬ СПОРИТ С ЧАЙНИКОМ", "ПЕЛЬМЕНЬ ПРОВАЛИЛ ЭКЗАМЕН", "ПЕЛЬМЕНЬ ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ПЕЛЬМЕНЬ ВЫЗВАЛ ДИРЕКТОРА", "ПЕЛЬМЕНЬ ОПЯТЬ НА СВЯЗИ", "ПЕЛЬМЕНЬ УВОЛЕН", "ПЕЛЬМЕНЬ ОБИДЕЛСЯ", "ПЕЛЬМЕНЬ ПРОПАЛ", "ПЕЛЬМЕНЬ ЗАДУМАЛСЯ", "ПЕЛЬМЕНЬ ЗАБЛУДИЛСЯ", "ПЕЛЬМЕНЬ ОПОЗДАЛ", "ПЕЛЬМЕНЬ НЕ ВЫВОЗИТ", "ПЕЛЬМЕНЬ УСТАЛ", "ПЕЛЬМЕНЬ ПЛАЧЕТ", "ПЕЛЬМЕНЬ НЕ ОДОБРЯЕТ", "ПЕЛЬМЕНЬ В ДОЛГАХ", "ПЕЛЬМЕНЬ НА БОЛЬНИЧНОМ", "БАТОН ОПЯТЬ НА СВЯЗИ", "БАТОН УВОЛЕН", "БАТОН ОБИДЕЛСЯ", "БАТОН ПРОПАЛ", "БАТОН ЗАДУМАЛСЯ", "БАТОН ЗАБЛУДИЛСЯ", "БАТОН ОПОЗДАЛ", "БАТОН НЕ ВЫВОЗИТ", "БАТОН УСТАЛ", "БАТОН ПЛАЧЕТ", "БАТОН НЕ ОДОБРЯЕТ", "БАТОН В ДОЛГАХ", "БАТОН НА БОЛЬНИЧНОМ", "БАТОН В ОТПУСКЕ", "БАТОН НА ПЕРЕСДАЧЕ", "БАТОН В ПРОБКЕ", "БАТОН НА СОБЕСЕДОВАНИИ", "БАТОН В ДЕКРЕТЕ", "БАТОН НА СОВЕЩАНИИ", "БАТОН В ЧАТЕ", "КАБАЧОК НЕ ВЫВОЗИТ", "КАБАЧОК УСТАЛ", "КАБАЧОК ПЛАЧЕТ", "КАБАЧОК НЕ ОДОБРЯЕТ", "КАБАЧОК В ДОЛГАХ", "КАБАЧОК НА БОЛЬНИЧНОМ", "КАБАЧОК В ОТПУСКЕ", "КАБАЧОК НА ПЕРЕСДАЧЕ", "КАБАЧОК В ПРОБКЕ", "КАБАЧОК НА СОБЕСЕДОВАНИИ", "КАБАЧОК В ДЕКРЕТЕ", "КАБАЧОК НА СОВЕЩАНИИ", "КАБАЧОК В ЧАТЕ", "КАБАЧОК НЕ ОТВЕЧАЕТ", "КАБАЧОК СДАЁТ ЕГЭ", "КАБАЧОК ПОТЕРЯЛ ПАСПОРТ", "КАБАЧОК ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "КАБАЧОК ПРОСИТ ОТПУСК", "КАБАЧОК ВЫЗВАЛ ТАКСИ", "КАБАЧОК ЗАБЫЛ ПАРОЛЬ", "ШАУРМА НА ПЕРЕСДАЧЕ", "ШАУРМА В ПРОБКЕ", "ШАУРМА НА СОБЕСЕДОВАНИИ", "ШАУРМА В ДЕКРЕТЕ", "ШАУРМА НА СОВЕЩАНИИ", "ШАУРМА В ЧАТЕ", "ШАУРМА НЕ ОТВЕЧАЕТ", "ШАУРМА СДАЁТ ЕГЭ", "ШАУРМА ПОТЕРЯЛ ПАСПОРТ", "ШАУРМА ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "ШАУРМА ПРОСИТ ОТПУСК", "ШАУРМА ВЫЗВАЛ ТАКСИ", "ШАУРМА ЗАБЫЛ ПАРОЛЬ", "ШАУРМА ВЫШЕЛ ИЗ ЧАТА", "ШАУРМА ОТКРЫЛ ИП", "ШАУРМА ЗАСТРЯЛ В ЛИФТЕ", "ШАУРМА КУПИЛ ГАРАЖ", "ШАУРМА ИЩЕТ СМЫСЛ", "ШАУРМА ЗАПИСАЛСЯ К ВРАЧУ", "ШАУРМА ВЫЗВАЛ САНТЕХНИКА", "ТАКСИСТ СДАЁТ ЕГЭ", "ТАКСИСТ ПОТЕРЯЛ ПАСПОРТ", "ТАКСИСТ ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "ТАКСИСТ ПРОСИТ ОТПУСК", "ТАКСИСТ ВЫЗВАЛ ТАКСИ", "ТАКСИСТ ЗАБЫЛ ПАРОЛЬ", "ТАКСИСТ ВЫШЕЛ ИЗ ЧАТА", "ТАКСИСТ ОТКРЫЛ ИП", "ТАКСИСТ ЗАСТРЯЛ В ЛИФТЕ", "ТАКСИСТ КУПИЛ ГАРАЖ", "ТАКСИСТ ИЩЕТ СМЫСЛ", "ТАКСИСТ ЗАПИСАЛСЯ К ВРАЧУ", "ТАКСИСТ ВЫЗВАЛ САНТЕХНИКА", "ТАКСИСТ ПОЛУЧИЛ ДВОЙКУ", "ТАКСИСТ ПОТЕРЯЛ ТАПОК", "ТАКСИСТ СПОРИТ С ЧАЙНИКОМ", "ТАКСИСТ ПРОВАЛИЛ ЭКЗАМЕН", "ТАКСИСТ ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ТАКСИСТ ВЫЗВАЛ ДИРЕКТОРА", "ТАКСИСТ ОПЯТЬ НА СВЯЗИ", "ГОЛУБЬ ОТКРЫЛ ИП", "ГОЛУБЬ ЗАСТРЯЛ В ЛИФТЕ", "ГОЛУБЬ КУПИЛ ГАРАЖ", "ГОЛУБЬ ИЩЕТ СМЫСЛ", "ГОЛУБЬ ЗАПИСАЛСЯ К ВРАЧУ", "ГОЛУБЬ ВЫЗВАЛ САНТЕХНИКА", "ГОЛУБЬ ПОЛУЧИЛ ДВОЙКУ", "ГОЛУБЬ ПОТЕРЯЛ ТАПОК", "ГОЛУБЬ СПОРИТ С ЧАЙНИКОМ", "ГОЛУБЬ ПРОВАЛИЛ ЭКЗАМЕН", "ГОЛУБЬ ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ГОЛУБЬ ВЫЗВАЛ ДИРЕКТОРА", "ГОЛУБЬ ОПЯТЬ НА СВЯЗИ", "ГОЛУБЬ УВОЛЕН", "ГОЛУБЬ ОБИДЕЛСЯ", "ГОЛУБЬ ПРОПАЛ", "ГОЛУБЬ ЗАДУМАЛСЯ", "ГОЛУБЬ ЗАБЛУДИЛСЯ", "ГОЛУБЬ ОПОЗДАЛ", "ГОЛУБЬ НЕ ВЫВОЗИТ", "ХОМЯК ПОТЕРЯЛ ТАПОК", "ХОМЯК СПОРИТ С ЧАЙНИКОМ", "ХОМЯК ПРОВАЛИЛ ЭКЗАМЕН", "ХОМЯК ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ХОМЯК ВЫЗВАЛ ДИРЕКТОРА", "ХОМЯК ОПЯТЬ НА СВЯЗИ", "ХОМЯК УВОЛЕН", "ХОМЯК ОБИДЕЛСЯ", "ХОМЯК ПРОПАЛ", "ХОМЯК ЗАДУМАЛСЯ", "ХОМЯК ЗАБЛУДИЛСЯ", "ХОМЯК ОПОЗДАЛ", "ХОМЯК НЕ ВЫВОЗИТ", "ХОМЯК УСТАЛ", "ХОМЯК ПЛАЧЕТ", "ХОМЯК НЕ ОДОБРЯЕТ", "ХОМЯК В ДОЛГАХ", "ХОМЯК НА БОЛЬНИЧНОМ", "ХОМЯК В ОТПУСКЕ", "ХОМЯК НА ПЕРЕСДАЧЕ", "КАРАСЬ ОБИДЕЛСЯ", "КАРАСЬ ПРОПАЛ", "КАРАСЬ ЗАДУМАЛСЯ", "КАРАСЬ ЗАБЛУДИЛСЯ", "КАРАСЬ ОПОЗДАЛ", "КАРАСЬ НЕ ВЫВОЗИТ", "КАРАСЬ УСТАЛ", "КАРАСЬ ПЛАЧЕТ", "КАРАСЬ НЕ ОДОБРЯЕТ", "КАРАСЬ В ДОЛГАХ", "КАРАСЬ НА БОЛЬНИЧНОМ", "КАРАСЬ В ОТПУСКЕ", "КАРАСЬ НА ПЕРЕСДАЧЕ", "КАРАСЬ В ПРОБКЕ", "КАРАСЬ НА СОБЕСЕДОВАНИИ", "КАРАСЬ В ДЕКРЕТЕ", "КАРАСЬ НА СОВЕЩАНИИ", "КАРАСЬ В ЧАТЕ", "КАРАСЬ НЕ ОТВЕЧАЕТ", "КАРАСЬ СДАЁТ ЕГЭ", "ДЕД ПЛАЧЕТ", "ДЕД НЕ ОДОБРЯЕТ", "ДЕД В ДОЛГАХ", "ДЕД НА БОЛЬНИЧНОМ", "ДЕД В ОТПУСКЕ", "ДЕД НА ПЕРЕСДАЧЕ", "ДЕД В ПРОБКЕ", "ДЕД НА СОБЕСЕДОВАНИИ", "ДЕД В ДЕКРЕТЕ", "ДЕД НА СОВЕЩАНИИ", "ДЕД В ЧАТЕ", "ДЕД НЕ ОТВЕЧАЕТ", "ДЕД СДАЁТ ЕГЭ", "ДЕД ПОТЕРЯЛ ПАСПОРТ", "ДЕД ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "ДЕД ПРОСИТ ОТПУСК", "ДЕД ВЫЗВАЛ ТАКСИ", "ДЕД ЗАБЫЛ ПАРОЛЬ", "ДЕД ВЫШЕЛ ИЗ ЧАТА", "ДЕД ОТКРЫЛ ИП", "ПЫЛЕСОС НА СОБЕСЕДОВАНИИ", "ПЫЛЕСОС В ДЕКРЕТЕ", "ПЫЛЕСОС НА СОВЕЩАНИИ", "ПЫЛЕСОС В ЧАТЕ", "ПЫЛЕСОС НЕ ОТВЕЧАЕТ", "ПЫЛЕСОС СДАЁТ ЕГЭ", "ПЫЛЕСОС ПОТЕРЯЛ ПАСПОРТ", "ПЫЛЕСОС ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "ПЫЛЕСОС ПРОСИТ ОТПУСК", "ПЫЛЕСОС ВЫЗВАЛ ТАКСИ", "ПЫЛЕСОС ЗАБЫЛ ПАРОЛЬ", "ПЫЛЕСОС ВЫШЕЛ ИЗ ЧАТА", "ПЫЛЕСОС ОТКРЫЛ ИП", "ПЫЛЕСОС ЗАСТРЯЛ В ЛИФТЕ", "ПЫЛЕСОС КУПИЛ ГАРАЖ", "ПЫЛЕСОС ИЩЕТ СМЫСЛ", "ПЫЛЕСОС ЗАПИСАЛСЯ К ВРАЧУ", "ПЫЛЕСОС ВЫЗВАЛ САНТЕХНИКА", "ПЫЛЕСОС ПОЛУЧИЛ ДВОЙКУ", "ПЫЛЕСОС ПОТЕРЯЛ ТАПОК", "ТАБУРЕТ ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ", "ТАБУРЕТ ПРОСИТ ОТПУСК", "ТАБУРЕТ ВЫЗВАЛ ТАКСИ", "ТАБУРЕТ ЗАБЫЛ ПАРОЛЬ", "ТАБУРЕТ ВЫШЕЛ ИЗ ЧАТА", "ТАБУРЕТ ОТКРЫЛ ИП", "ТАБУРЕТ ЗАСТРЯЛ В ЛИФТЕ", "ТАБУРЕТ КУПИЛ ГАРАЖ", "ТАБУРЕТ ИЩЕТ СМЫСЛ", "ТАБУРЕТ ЗАПИСАЛСЯ К ВРАЧУ", "ТАБУРЕТ ВЫЗВАЛ САНТЕХНИКА", "ТАБУРЕТ ПОЛУЧИЛ ДВОЙКУ", "ТАБУРЕТ ПОТЕРЯЛ ТАПОК", "ТАБУРЕТ СПОРИТ С ЧАЙНИКОМ", "ТАБУРЕТ ПРОВАЛИЛ ЭКЗАМЕН", "ТАБУРЕТ ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ТАБУРЕТ ВЫЗВАЛ ДИРЕКТОРА", "ТАБУРЕТ ОПЯТЬ НА СВЯЗИ", "ТАБУРЕТ УВОЛЕН", "ТАБУРЕТ ОБИДЕЛСЯ", "ЧАЙНИК КУПИЛ ГАРАЖ", "ЧАЙНИК ИЩЕТ СМЫСЛ", "ЧАЙНИК ЗАПИСАЛСЯ К ВРАЧУ", "ЧАЙНИК ВЫЗВАЛ САНТЕХНИКА", "ЧАЙНИК ПОЛУЧИЛ ДВОЙКУ", "ЧАЙНИК ПОТЕРЯЛ ТАПОК", "ЧАЙНИК СПОРИТ С ЧАЙНИКОМ", "ЧАЙНИК ПРОВАЛИЛ ЭКЗАМЕН", "ЧАЙНИК ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ЧАЙНИК ВЫЗВАЛ ДИРЕКТОРА", "ЧАЙНИК ОПЯТЬ НА СВЯЗИ", "ЧАЙНИК УВОЛЕН", "ЧАЙНИК ОБИДЕЛСЯ", "ЧАЙНИК ПРОПАЛ", "ЧАЙНИК ЗАДУМАЛСЯ", "ЧАЙНИК ЗАБЛУДИЛСЯ", "ЧАЙНИК ОПОЗДАЛ", "ЧАЙНИК НЕ ВЫВОЗИТ", "ЧАЙНИК УСТАЛ", "ЧАЙНИК ПЛАЧЕТ", "ШЛАГБАУМ ПРОВАЛИЛ ЭКЗАМЕН", "ШЛАГБАУМ ОТМЕНИЛ ПОНЕДЕЛЬНИК", "ШЛАГБАУМ ВЫЗВАЛ ДИРЕКТОРА", "ШЛАГБАУМ ОПЯТЬ НА СВЯЗИ", "ШЛАГБАУМ УВОЛЕН", "ШЛАГБАУМ ОБИДЕЛСЯ", "ШЛАГБАУМ ПРОПАЛ", "ШЛАГБАУМ ЗАДУМАЛСЯ", "ШЛАГБАУМ ЗАБЛУДИЛСЯ", "ШЛАГБАУМ ОПОЗДАЛ", "ШЛАГБАУМ НЕ ВЫВОЗИТ", "ШЛАГБАУМ УСТАЛ", "ШЛАГБАУМ ПЛАЧЕТ", "ШЛАГБАУМ НЕ ОДОБРЯЕТ", "ШЛАГБАУМ В ДОЛГАХ", "ШЛАГБАУМ НА БОЛЬНИЧНОМ", "ШЛАГБАУМ В ОТПУСКЕ", "ШЛАГБАУМ НА ПЕРЕСДАЧЕ", "ШЛАГБАУМ В ПРОБКЕ", "ШЛАГБАУМ НА СОБЕСЕДОВАНИИ", "БУТЕРБРОД ЗАДУМАЛСЯ", "БУТЕРБРОД ЗАБЛУДИЛСЯ", "БУТЕРБРОД ОПОЗДАЛ", "БУТЕРБРОД НЕ ВЫВОЗИТ", "БУТЕРБРОД УСТАЛ", "БУТЕРБРОД ПЛАЧЕТ", "БУТЕРБРОД НЕ ОДОБРЯЕТ", "БУТЕРБРОД В ДОЛГАХ", "БУТЕРБРОД НА БОЛЬНИЧНОМ", "БУТЕРБРОД В ОТПУСКЕ", "БУТЕРБРОД НА ПЕРЕСДАЧЕ", "БУТЕРБРОД В ПРОБКЕ", "БУТЕРБРОД НА СОБЕСЕДОВАНИИ", "БУТЕРБРОД В ДЕКРЕТЕ", "БУТЕРБРОД НА СОВЕЩАНИИ", "БУТЕРБРОД В ЧАТЕ", "БУТЕРБРОД НЕ ОТВЕЧАЕТ", "БУТЕРБРОД СДАЁТ ЕГЭ", "БУТЕРБРОД ПОТЕРЯЛ ПАСПОРТ", "БУТЕРБРОД ПИШЕТ ОБЪЯСНИТЕЛЬНУЮ"]
    top, bottom = "", random.choice(captions)
    with connect() as c:
        recent = {str(row[0]) for row in c.execute(
            "SELECT template_id FROM real_meme_sent WHERE user_id=?", (uid,)).fetchall()}
    attempts = []

    def try_photo(template, credit):
        try:
            data = _source_bytes(template)
            result = compose(data, top, bottom, serial, credit)
            return serial, str(template["id"]), result, joke["id"]
        except Exception as exc:
            attempts.append(f"{template.get('id', 'unknown')}: {type(exc).__name__}: {exc}")
            print(f"Photomeme template failed ({template.get('id', '?')}): "
                  f"{type(exc).__name__}: {exc}", flush=True)
            return None

    # Each source is isolated; failure to enumerate one is not a reason to
    # skip the other banks. Do not contact Imgflip when a local frame works.
    try:
        local = reaction_bank.templates(
            risk, seen=recent, limit=32, desired_mood=joke["mood"],
            desired_scenes=joke_catalog.scene_tags(joke["text"], risk))
    except Exception as exc:
        local = []
        attempts.append(f"reaction_bank: {type(exc).__name__}: {exc}")
        print(f"Reaction photo bank failed: {type(exc).__name__}: {exc}", flush=True)
    for item in local[:16]:
        result = try_photo(item, "Кадр из коллекции")
        if result is not None:
            return result

    try:
        legacy = mellstroy_bank.templates(risk, seen=recent, limit=32)
    except Exception as exc:
        legacy = []
        attempts.append(f"mellstroy_bank: {type(exc).__name__}: {exc}")
        print(f"Legacy Mellstroy bank failed: {type(exc).__name__}: {exc}",
              flush=True)
    for item in legacy[:16]:
        result = try_photo(item, "Кадр из видео")
        if result is not None:
            return result

    try:
        approved = _curated_templates()
    except Exception as exc:
        approved = []
        attempts.append(f"approved_photos: {type(exc).__name__}: {exc}")
        print(f"Approved photo directory failed: {type(exc).__name__}: {exc}",
              flush=True)
    random.shuffle(approved)
    approved.sort(key=lambda item: (
        1 if item.get("id") in recent else 0,
        0 if item.get("risk") in (risk, "general") else 1,
    ))
    for item in approved[:12]:
        result = try_photo(item, "Фото автора")
        if result is not None:
            return result

    # Owner explicitly opting into AI authorizes a billed image call.
    # Never use a generated likeness of a real person.
    if allow_paid and _budget(connect):
        try:
            photo = _ai_image(risk, serial)
            return serial, "ai", compose(photo, top, bottom, serial, "AI фото"), joke["id"]
        except Exception as exc:
            attempts.append(f"ai: {type(exc).__name__}: {exc}")
            print(f"Optional photo generator failed: {type(exc).__name__}: {exc}",
                  flush=True)

    try:
        catalog = _top_templates()
    except Exception as exc:
        catalog = []
        attempts.append(f"imgflip: {type(exc).__name__}: {exc}")
        print(f"Imgflip catalog unavailable: {type(exc).__name__}: {exc}", flush=True)

    # Paid Premium search remains opt-in, limited and cached.
    try:
        catalog += _premium_templates(connect)
    except Exception as exc:
        attempts.append(f"premium: {type(exc).__name__}: {exc}")
        print(f"Premium meme catalog unavailable: {type(exc).__name__}: {exc}",
              flush=True)
    random.shuffle(catalog)
    catalog.sort(key=lambda t: 1 if str(t["id"]) in recent else 0)
    for item in catalog[:8]:
        result = try_photo(item, "Imgflip")
        if result is not None:
            return result

    if attempts:
        # Full reasons are printed to the service journal by the caller.
        detail = "; ".join(attempts[-5:])
        raise RuntimeError(f"No usable photo source. {detail}")
    raise RuntimeError("Photo libraries are all empty. "
                       "Import images with /memes_add or check Imgflip connectivity.")


def source_diagnostics(connect):
    """Safe, key-free diagnostic available to the Telegram administrator.

    Checks an actual JPEG caption using only local photo sources, no downloads,
    AI calls or changes to user sessions.
    """
    info = {}
    for label, getter in (
        ("reaction", lambda: reaction_bank.templates("general", limit=3)),
        ("mellstroy", lambda: mellstroy_bank.templates("general", limit=3)),
        ("approved", _curated_templates),
    ):
        try:
            pictures = getter()
            info[label] = {"available": len(pictures), "image_ok": False,
                           "error": ""}
            if pictures:
                try:
                    photo = _source_bytes(pictures[0])
                    test = compose(photo, "", "БЛЯТЬ, ЭТО ТЕСТ", 1, label)
                    with Image.open(io.BytesIO(test)) as image:
                        image.verify()
                    info[label]["image_ok"] = True
                except Exception as exc:
                    info[label]["error"] = f"{type(exc).__name__}: {str(exc)[:140]}"
        except Exception as exc:
            info[label] = {"available": 0, "image_ok": False,
                           "error": f"{type(exc).__name__}: {str(exc)[:140]}"}
    try:
        info["jokes"] = joke_catalog.count()
    except Exception as exc:
        info["jokes_error"] = f"{type(exc).__name__}: {str(exc)[:140]}"
    return info


def _keyboard(key):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="😂 Зашло",callback_data=f"mv:1:{key}"),
         InlineKeyboardButton(text="🥱 Не смешно",callback_data=f"mv:-1:{key}")],
        [InlineKeyboardButton(text="🎲 Ещё мем",callback_data="mnext"),
         InlineKeyboardButton(text="🎯 Темы",callback_data="v2:memethemes")],
    ])

def _record(connect,uid,serial,source,photo,text,joke_id=None):
    key=hashlib.sha256(f"{uid}:{serial}:{source}".encode()).hexdigest()[:16]
    today=datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as c:
        c.execute("""INSERT INTO meme_sends(user_id,meme_key,at)
                     VALUES(?,?,?)""",(uid,key,today))
        c.execute("""INSERT INTO real_meme_sent
                     (user_id,serial,template_id,punchline,created_at)
                     VALUES(?,?,?,?,?)""",(uid,serial,str(source),text,today))
        # Deliberately no fixed-size history purge: a growing image library
        # should not repeat a frame after the first arbitrary 1200 sends.
    if joke_id:
        joke_catalog.mark_sent(connect,uid,joke_id,serial,meme_key=key)
    return key

async def send(message,uid,substance,risk,connect,*args,**kwargs):
    """Only send genuine photo memes or show a short actionable source error."""
    try:
        prepared=await asyncio.to_thread(_prepare,connect,uid,substance,risk,True)
        serial,source,payload=prepared[:3]
        joke_id=prepared[3] if len(prepared)>3 else None
        key=hashlib.sha256(f"{uid}:{serial}:{source}".encode()).hexdigest()[:16]
        await message.answer_photo(BufferedInputFile(payload,filename=f"meme-{serial}.jpg"),
                                   caption="🎭 Старый добрый мем",
                                   reply_markup=_keyboard(key))
        _record(connect,uid,serial,source,payload,str(risk),joke_id=joke_id)
        return True
    except Exception as exc:
        import traceback
        print(f"Photomeme delivery failed: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        await message.answer(
            "❌ Фотомем не отправился. Возможна ошибка файла, шрифта "
            "или доступа к библиотеке — не обязательно интернета.\n"
            "Администратор: /memes_diag или журнал сервиса.\n"
            "Добавить свои кадры: фото/видео с подписью "
            "/memes_add YES Имя laugh."
        )
        return False


async def send_bot(bot,uid,substance,risk,connect,caption,reply_markup):
    # Never incur paid AI image charges for an automatic 10-minute reminder.
    prepared=await asyncio.to_thread(_prepare,connect,uid,substance,risk,False)
    serial,source,payload=prepared[:3]
    joke_id=prepared[3] if len(prepared)>3 else None
    await bot.send_photo(uid,BufferedInputFile(payload,filename=f"meme-{serial}.jpg"),
                         caption=caption[:1024],reply_markup=reply_markup)
    _record(connect,uid,serial,source,payload,str(risk),joke_id=joke_id)
    return True
