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
    """Select a fresh one-sentence dark joke and a semantically matched REAL photo.

    Priority: unlimited multi-person bank -> existing Mellstroy 600 bank ->
    legally curated photo templates -> filtered Imgflip -> optional paid AI.
    """
    serial = meme_engine.next_serial(connect, uid)
    risk = risk if risk in LINES else "general"
    joke = joke_catalog.select(connect, uid, risk, serial)
    sentence = joke["text"].rstrip(".")
    # Each picture has ONE sentence, not two unrelated captions.
    top, bottom = "", sentence
    with connect() as c:
        recent = {str(row[0]) for row in c.execute(
            "SELECT template_id FROM real_meme_sent WHERE user_id=?",
            (uid,)).fetchall()}

    real = reaction_bank.templates(risk, seen=recent, limit=32,
                                   desired_mood=joke["mood"])
    if not real:
        real = mellstroy_bank.templates(risk, seen=recent, limit=32)
    using_real = bool(real)
    if using_real:
        templates = real
    else:
        templates = _curated_templates()
        try:
            templates += _top_templates()
        except Exception as exc:
            print(f"Imgflip catalog unavailable: {type(exc).__name__}: {exc}",
                  flush=True)
        templates += _premium_templates(connect)

        def rank(t):
            curated = "path" in t
            matches = t.get("risk") in (risk, "general")
            return 0 if curated and matches else 1 if not curated else 2
        random.shuffle(templates)
        templates.sort(key=lambda t: (1 if t["id"] in recent else 0,
                                      rank(t)))

    # Real people's faces are never generated or changed by an AI.
    if not using_real and allow_paid and _budget(connect):
        try:
            photo = _ai_image(risk, serial)
            return serial, "ai", compose(photo, top, bottom, serial, "AI фото"), joke["id"]
        except Exception as exc:
            print(f"Photo provider failed: {type(exc).__name__}: {exc}",
                  flush=True)
    for tpl in templates[:8 if using_real else 4]:
        try:
            photo = _source_bytes(tpl)
            identifier = str(tpl["id"])
            source_label = ("Кадр из коллекции" if identifier.startswith("reaction-")
                            else "Кадр из видео" if identifier.startswith("mellstroy-")
                            else "Imgflip" if "url" in tpl else "Фото автора")
            payload = compose(photo, top, bottom, serial, source_label)
            return serial, identifier, payload, joke["id"]
        except Exception as exc:
            print(f"Photo unavailable {tpl['id']}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
    raise RuntimeError("Нет подходящих фото; загрузи разрешённые фотографии "
                       "через /memes_add или проверь интернет.")

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
        print(f"Photomeme delivery failed: {type(exc).__name__}: {exc}",flush=True)
        await message.answer("😕 Не удалось загрузить фотомем. Проверь интернет на сервере "
                             "или добавь проверенные фотографии через /addmeme. "
                             "Пока не буду подменять их странными мультяшками.")
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
