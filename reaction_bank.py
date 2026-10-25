"""Unlimited, multi-person reaction PHOTO bank for Telegram and local videos.

An image's owner-supplied person label is NOT an identity prediction.
Optional paid vision describes only visible reaction, pose and scene; its
default is OFF until the admin explicitly enables it. Otherwise filenames,
folder names and visible image properties supply conservative metadata.
No generated face, face alteration, random photo scraping or frame cap.

Import:
  python reaction_bank.py import /srv/videos --person "Меллстрой" --confirm-rights
  python reaction_bank.py status
  python reaction_bank.py retag --limit 100
  python reaction_bank.py sample
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from contextlib import contextmanager
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
BANK = ROOT / "data" / "reaction_bank"
DB = BANK / "reactions.sqlite"
VIDEOS = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
PHOTOS = {".jpg", ".jpeg", ".png", ".webp"}
MOODS = ("shock", "laugh", "yell", "smug", "confused", "tired",
         "suspicious", "angry", "sad", "neutral")
RISK_MOODS = {
    "general": ("confused", "shock", "laugh", "yell"),
    "more": ("smug", "laugh", "confused"),
    "ex": ("shock", "sad", "confused"),
    "contacts": ("yell", "laugh", "smug"),
    "money": ("shock", "sad", "angry"),
    "sleep": ("tired", "sad", "confused"),
    "paranoia": ("suspicious", "shock", "confused"),
    "travel": ("confused", "smug", "shock"),
    "after": ("sad", "tired", "shock"),
    "check": ("smug", "laugh", "neutral"),
    "ok": ("smug", "laugh", "neutral"),
}
HINTS = {
    "shock": ("шок", "офиг", "ахуй", "wtf", "shock", "surprise"),
    "laugh": ("смех", "сме", "рж", "laugh", "lol", "smile"),
    "yell": ("орёт", "крик", "ор_", "yell", "scream", "rage"),
    "smug": ("довол", "хитр", "кайф", "smug", "proud", "grin"),
    "confused": ("чё", "непон", "туп", "huh", "confused"),
    "tired": ("устал", "спит", "сон", "tired", "sleep"),
    "suspicious": ("подозр", "взгляд", "sideeye", "suspicious"),
    "angry": ("злой", "злость", "angry", "anger", "mad"),
    "sad": ("груст", "плач", "sad", "cry", "despair"),
}
SCENE_NAMES = {"portrait", "desk", "microphone", "phone", "car", "street",
               "bed", "kitchen", "group", "gaming", "closeup", "other"}
MAX_INPUT_PIXELS = 26_000_000
# Resource limits are operational protection, NOT a limit on library frame COUNT.
SAFETY_DISK_FREE = 180 * 1024 * 1024
LOCK = threading.RLock()


def _db():
    BANK.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS reactions(
                 id TEXT PRIMARY KEY, file TEXT UNIQUE NOT NULL,
                 person TEXT NOT NULL, mood TEXT NOT NULL DEFAULT 'neutral',
                 scenes TEXT NOT NULL DEFAULT '[]',
                 dhash TEXT NOT NULL, source TEXT NOT NULL, credit TEXT NOT NULL,
                 tag_method TEXT NOT NULL DEFAULT 'filename',
                 confidence REAL NOT NULL DEFAULT 0,
                 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                 )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_reactions_mood ON reactions(mood)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_reactions_person ON reactions(person)")
    c.execute("""CREATE TABLE IF NOT EXISTS vision_usage(
                 day TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0)""")
    return c


@contextmanager
def db_session():
    """Always close reaction-bank connections in a long-running Telegram bot."""
    c=_db()
    try:
        with c:
            yield c
    finally:
        c.close()


def _dhash(img):
    small=img.convert("L").resize((9,8),Image.Resampling.BILINEAR)
    pixels=list(small.getdata())
    h=0
    for y in range(8):
        for x in range(8):
            h=(h<<1) | int(pixels[y*9+x]>pixels[y*9+x+1])
    return h


class NearDuplicateIndex:
    """Fast exact/near duplicate lookup even for a very large catalog.

    Pigeonhole: if <=7 differing bits in 64, one of eight 8-bit partitions
    must be identical; only those hash candidates require exact Hamming checks.
    """
    def __init__(self):
        self.buckets=defaultdict(set)
        self.hashes=set()

    def add(self,value):
        value=int(value)
        if value in self.hashes: return
        self.hashes.add(value)
        for i in range(8):
            self.buckets[(i,(value>>(8*i))&255)].add(value)

    def similar(self,value,max_distance=6):
        if value in self.hashes:
            return True
        maybe=set()
        for i in range(8):
            maybe.update(self.buckets.get((i,(value>>(8*i))&255),()))
        return any((value^candidate).bit_count()<=max_distance for candidate in maybe)


def _human_label(value):
    value=re.sub(r"[^\w .ёЁ-]","",str(value or ""),flags=re.UNICODE).strip()
    return value[:45] or "без имени"


def _metadata_tag(path, person, override="auto"):
    if override != "auto":
        if override not in MOODS:
            raise ValueError("Unknown reaction mood: "+override)
        return override, "manual", 1.0
    # Filename tagging is coarse metadata, not real emotion detection.
    title=(path.parent.name+" "+path.stem+" "+person).casefold()
    for tag, words in HINTS.items():
        if any(w in title for w in words):
            return tag, "filename", .55
    return "neutral", "undetermined", 0.0


def _image_quality(img):
    if min(img.size)<275:
        return False
    small=img.convert("L").resize((320,240),Image.Resampling.BILINEAR)
    stats=ImageStat.Stat(small)
    if stats.mean[0]<15 or stats.mean[0]>245 or stats.stddev[0]<9:
        return False
    return ImageStat.Stat(small.filter(ImageFilter.FIND_EDGES)).stddev[0]>6


def _visual_snapshot(img):
    data=io.BytesIO()
    photo=img.copy()
    photo.thumbnail((550,550),Image.Resampling.LANCZOS)
    photo.save(data,"JPEG",quality=69)
    return data.getvalue()


def _reserve_vision(c):
    if os.getenv("REACTION_VISION_ENABLED","0")!="1":
        return False
    if not os.getenv("OPENAI_API_KEY"):
        return False
    try:
        cap=int(os.getenv("REACTION_VISION_DAILY_LIMIT","120"))
    except ValueError:
        cap=120
    # 0 disables; no hidden unbounded paid requests.
    if cap<=0:
        return False
    day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row=c.execute("SELECT requests FROM vision_usage WHERE day=?",(day,)).fetchone()
    if row and int(row[0])>=cap:
        return False
    c.execute("""INSERT INTO vision_usage(day,requests) VALUES(?,1)
                 ON CONFLICT(day) DO UPDATE SET requests=requests+1""",(day,))
    return True


def _vision_tags(img):
    """Optional paid vision; do not infer person identity or drug use."""
    snapshot=_visual_snapshot(img)
    image_url="data:image/jpeg;base64,"+base64.b64encode(snapshot).decode("ascii")
    request_body={
        "model": os.getenv("REACTION_VISION_MODEL","gpt-4.1-mini"),
        "max_tokens": 190,
        "temperature": 0,
        "response_format": {"type":"json_object"},
        "messages": [{
            "role":"user",
            "content":[
                {"type":"text","text":(
                    "Describe only visible facial expression/pose and scene for "
                    "editorial meme image retrieval. Do NOT identify anyone, "
                    "infer drug use or mental health, or assert actual feelings. "
                    "Reply strict JSON with keys: reaction (one of shock, laugh, "
                    "yell, smug, confused, tired, suspicious, angry, sad, neutral), "
                    "scene_tags (array drawn from portrait, desk, microphone, "
                    "phone, car, street, bed, kitchen, group, gaming, closeup, other), "
                    "confidence (0..1). Neutral if expression is unclear."
                )},
                {"type":"image_url","image_url":{"url":image_url,"detail":"low"}}
            ]
        }]
    }
    data=json.dumps(request_body).encode("utf-8")
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",
             data=data,method="POST",headers={
                 "Authorization":"Bearer "+os.environ["OPENAI_API_KEY"],
                 "Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=25) as response:
        reply=json.loads(response.read(150_000))
    parsed=json.loads(reply["choices"][0]["message"]["content"])
    mood=parsed.get("reaction","neutral")
    if mood not in MOODS:
        mood="neutral"
    tags=[x for x in parsed.get("scene_tags",[]) if x in SCENE_NAMES][:5]
    confidence=max(0.,min(1.,float(parsed.get("confidence",0))))
    return mood,tags,confidence


def _auto_tags(c,img,mood,method,confidence):
    if not _reserve_vision(c):
        return mood,[],method,confidence
    # Commit quota reservation before the external request, including errors.
    c.commit()
    try:
        m,t,conf=_vision_tags(img)
        return m,t,"vision",conf
    except Exception as exc:
        print(f"Reaction vision tag failed: {type(exc).__name__}: {str(exc)[:90]}",
              flush=True)
        return mood,[],method,confidence


def _video_images(path,temp):
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not installed; sudo apt-get install -y ffmpeg")
    pattern=temp/"out_%08d.jpg"
    cmd=[ffmpeg,"-hide_banner","-loglevel","error","-nostdin","-y",
         "-i",str(path),"-vf","fps=1,scale='min(1100,iw)':-2",
         "-q:v","4",str(pattern)]
    # No -frames:v CAP. Operates on all available timeline frames, subject
    # to disk space, subprocess timeout and source quality.
    subprocess.run(cmd,capture_output=True,check=True,timeout=1800)
    return sorted(temp.glob("out_*.jpg"))


def import_media(input_path, *, person="", mood="auto",
                 confirm_rights=False, credit="Admin-approved media",
                 min_distance=6, auto_tag=True):
    """Import an arbitrary number of photos/frames for any manually named person.

    No global quota, no per-person cap and no fictitious face cloning.
    Long footage can be split into smaller jobs if resources are insufficient.
    """
    if not confirm_rights:
        raise PermissionError("Explicit authorization / YES is required")
    if mood not in ("auto",)+MOODS:
        raise ValueError("Unknown mood")
    if not 0<=min_distance<=7:
        raise ValueError("min_distance must be 0..7 for exact indexed dedup")
    src=Path(input_path).expanduser().resolve()
    if src.is_file():
        sources=[src]
    elif src.is_dir():
        sources=sorted(p for p in src.rglob("*")
                       if p.is_file() and p.suffix.lower() in PHOTOS|VIDEOS)
    else:
        raise FileNotFoundError(src)
    if not sources:
        raise ValueError("No supported photos or videos")
    c=_db()
    hashes=NearDuplicateIndex()
    for row in c.execute("SELECT dhash FROM reactions"):
        try:hashes.add(int(row[0],16))
        except (TypeError,ValueError):pass
    added=0;duplicate=0;rejected=0;errors=[];total_candidates=0
    person=_human_label(person)
    with LOCK:
        try:
            for source in sources:
                detected,tag_method,conf=_metadata_tag(source,person,mood)
                try:
                    with tempfile.TemporaryDirectory(prefix="reaction_extract_") as tmp:
                        candidates=(_video_images(source,Path(tmp))
                                    if source.suffix.lower() in VIDEOS else [source])
                        for candidate in candidates:
                            total_candidates+=1
                            if shutil.disk_usage(BANK).free<SAFETY_DISK_FREE:
                                errors.append("Мало места на диске, импорт остановлен безопасно")
                                raise RuntimeError("low disk space")
                            try:
                                with Image.open(candidate) as original:
                                    if original.width*original.height>MAX_INPUT_PIXELS:
                                        rejected+=1;continue
                                    img=ImageOps.exif_transpose(original).convert("RGB")
                                    img.thumbnail((1100,1100),Image.Resampling.LANCZOS)
                                if not _image_quality(img):
                                    rejected+=1;continue
                                dh=_dhash(img)
                                if hashes.similar(dh,max_distance=min_distance):
                                    duplicate+=1;continue
                                output=io.BytesIO()
                                img.save(output,"JPEG",quality=87,optimize=True)
                                raw=output.getvalue()
                                key=hashlib.sha256(raw).hexdigest()[:20]
                                path=BANK/("shot_"+key+".jpg")
                                if path.exists():
                                    duplicate+=1;continue
                                chosen_mood,scenes,method,confidence=(
                                    _auto_tags(c,img,detected,tag_method,conf)
                                    if auto_tag and mood=="auto" else
                                    (detected,[],tag_method,conf))
                                tmpfile=path.with_suffix(".jpg.tmp")
                                try:
                                    tmpfile.write_bytes(raw)
                                    os.replace(tmpfile,path)
                                    c.execute("""INSERT INTO reactions
                                         (id,file,person,mood,scenes,dhash,source,
                                          credit,tag_method,confidence)
                                         VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                        (key,path.name,person,chosen_mood,
                                         json.dumps(scenes,ensure_ascii=False),
                                         f"{dh:016x}",source.name,credit,method,confidence))
                                    c.commit()
                                    hashes.add(dh)
                                    added+=1
                                finally:
                                    tmpfile.unlink(missing_ok=True)
                            except (OSError,ValueError,UnidentifiedImageError,
                                    sqlite3.Error) as exc:
                                rejected+=1
                                c.rollback()
                                if len(errors)<10:errors.append(
                                    f"{candidate.name}: {type(exc).__name__}: {str(exc)[:70]}")
                except (OSError,subprocess.SubprocessError,RuntimeError) as exc:
                    if len(errors)<10:
                        errors.append(f"{source.name}: {type(exc).__name__}: {str(exc)[:100]}")
                    if "disk space" in str(exc):break
        finally:
            c.commit()
            total=c.execute("SELECT COUNT(*) FROM reactions").fetchone()[0]
            c.close()
    return {"added":added,"total":total,"duplicates":duplicate,
            "rejected":rejected,"examined":total_candidates,"person":person,
            "sources":len(sources),"errors":errors}


def status():
    with db_session() as c:
        total=c.execute("SELECT COUNT(*) FROM reactions").fetchone()[0]
        people={r[0]:r[1] for r in c.execute(
            "SELECT person,COUNT(*) FROM reactions GROUP BY person ORDER BY COUNT(*) DESC")}
        moods={r[0]:r[1] for r in c.execute(
            "SELECT mood,COUNT(*) FROM reactions GROUP BY mood")}
        modes={r[0]:r[1] for r in c.execute(
            "SELECT tag_method,COUNT(*) FROM reactions GROUP BY tag_method")}
    return {"count":total,"people":people,"moods":moods,"tagging":modes,
            "vision_enabled":os.getenv("REACTION_VISION_ENABLED","0")=="1"}


def templates(risk, seen=None, limit=24, person=None, desired_mood=None):
    """Return genuine local media: match scene's editorial reaction to joke tag."""
    seen=seen or set()
    wanted=RISK_MOODS.get(risk,RISK_MOODS["general"])
    if desired_mood in MOODS and desired_mood != "neutral":
        wanted=(desired_mood,)+tuple(m for m in wanted if m != desired_mood)
    with db_session() as c:
        rows=[dict(r) for r in c.execute(
            "SELECT id,file,person,mood,scenes,credit FROM reactions"
            +(" WHERE person=?" if person else ""),
            (person,) if person else ())]
    if not rows:
        return []
    fresh=[r for r in rows if "reaction-"+r["id"] not in seen]
    rows=fresh if fresh else rows
    random.shuffle(rows)
    def rank(row):
        mood=row["mood"]
        return (0 if mood in wanted else 1 if mood=="neutral" else 2,
                wanted.index(mood) if mood in wanted else 99)
    rows.sort(key=rank)
    return [{"id":"reaction-"+r["id"],
             "path":BANK/r["file"],
             "name":r["person"],
             "person":r["person"],
             "mood":r["mood"],
             "scenes":r["scenes"],
             "risk":risk,
             "credit":r["credit"]}
            for r in rows[:limit]]


def retag_frame(frame_id,new_mood):
    if new_mood not in MOODS:
        raise ValueError("Mood must be one of: "+", ".join(MOODS))
    if not re.fullmatch(r"[0-9a-f]{8,20}",frame_id):
        raise ValueError("Invalid frame ID")
    with db_session() as c:
        result=c.execute("UPDATE reactions SET mood=?,tag_method='manual',confidence=1"
                          " WHERE id LIKE ?",(new_mood,frame_id+"%"))
        if result.rowcount!=1:
            raise ValueError("Frame not found or prefix is not unique")
    return True


def auto_retag(max_frames=None):
    """Opt-in batch photo inspection, respecting the paid vision daily budget."""
    if os.getenv("REACTION_VISION_ENABLED","0")!="1":
        return {"tagged":0,"warning":"Set REACTION_VISION_ENABLED=1 with an API key"}
    tagged=0
    with db_session() as c:
        rows=list(c.execute("SELECT id,file FROM reactions"
                            " WHERE tag_method!='manual' AND tag_method!='vision'"
                            " ORDER BY created_at"))
        for row in rows:
            if max_frames is not None and tagged>=max_frames:
                break
            try:
                with Image.open(BANK/row["file"]) as image:
                    photo=image.convert("RGB")
                if not _reserve_vision(c):
                    break
                c.commit()
                mood,tags,confidence=_vision_tags(photo)
                c.execute("""UPDATE reactions SET mood=?,scenes=?,tag_method='vision',
                             confidence=? WHERE id=?""",
                          (mood,json.dumps(tags),confidence,row["id"]))
                c.commit()
                tagged+=1
            except Exception as exc:
                print(f"Tag failed for frame {row['id']}: {type(exc).__name__}",flush=True)
    return {"tagged":tagged,"total":status()["count"]}


def sample(n=4):
    return templates("general",limit=n)


def main(argv=None):
    p=argparse.ArgumentParser(description="Unlimited multi-person reaction photo bank")
    subs=p.add_subparsers(dest="action",required=True)
    command=subs.add_parser("import")
    command.add_argument("source")
    command.add_argument("--person",default="без имени")
    command.add_argument("--mood",choices=("auto",)+MOODS,default="auto")
    command.add_argument("--confirm-rights",action="store_true")
    command.add_argument("--min-distance",type=int,default=6)
    subs.add_parser("status")
    sub=subs.add_parser("tag")
    sub.add_argument("id")
    sub.add_argument("mood",choices=MOODS)
    sub=subs.add_parser("retag")
    sub.add_argument("--limit",type=int,default=None)
    args=p.parse_args(argv)
    if args.action=="import":
        result=import_media(args.source,person=args.person,mood=args.mood,
                            confirm_rights=args.confirm_rights,
                            min_distance=args.min_distance)
    elif args.action=="status":
        result=status()
    elif args.action=="tag":
        retag_frame(args.id,args.mood)
        result={"updated":args.id,"mood":args.mood}
    else:
        result=auto_retag(args.limit)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
