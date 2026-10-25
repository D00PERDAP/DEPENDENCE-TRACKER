"""Mellstroy REAL-frame library: permission-confirmed footage -> up to 600 JPEGs.

NO synthetic faces, AI modifications, face-swaps, or stretched portraits.
Photos retain the real video frame, resized proportionally; photo memes get
their captions from real_memes.compose() after matching the frame's mood.

CLI:
  python mellstroy_bank.py import /path/to/cleared-clip.mp4 --confirm-rights
  python mellstroy_bank.py import /path/to/videos --target 600 --confirm-rights
  python mellstroy_bank.py status
  python mellstroy_bank.py contact-sheet

The source must be supplied by its rights holder, authorized uploader, or
otherwise legally reusable. A public stream link alone is not proof of rights.
Do not put video files or the real frame bank into GitHub.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
BANK = ROOT / "data" / "mellstroy_frames"
CATALOG = BANK / "catalog.json"
TARGET = 600
ALLOWED_VIDEO = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".webp"}
MAX_PIXELS = 25_000_000
MAX_SOURCE_BYTES = 750 * 1024 * 1024
LOCK = threading.RLock()
INDEX_CACHE = {"mtime": None, "items": []}

MOODS = ("shock", "laugh", "yell", "smug", "confused",
         "tired", "suspicious", "angry", "sad", "neutral")
MOOD_HINTS = {
    "shock": ("шок", "ахуй", "офиг", "wtf", "shock", "surprise"),
    "laugh": ("сме", "рж", "смех", "laugh", "smile", "happy"),
    "yell": ("ор", "крик", "орёт", "scream", "shout", "yell", "rage"),
    "smug": ("довол", "кайф", "хитр", "smug", "proud", "grin"),
    "confused": ("туп", "чё", "что", "confused", "huh", "puzzled"),
    "tired": ("устал", "спит", "сон", "tired", "sleep", "sad"),
    "suspicious": ("подозр", "взгляд", "suspicious", "sideeye"),
    "angry": ("злой", "бесит", "anger", "angry", "mad"),
    "sad": ("груст", "плач", "cry", "sad", "despair"),
}
RISK_MOODS = {
    "more": ("smug", "laugh", "confused"),
    "ex": ("shock", "confused", "sad"),
    "contacts": ("yell", "laugh", "smug"),
    "money": ("shock", "sad", "angry"),
    "sleep": ("tired", "sad", "confused"),
    "paranoia": ("suspicious", "shock", "confused"),
    "travel": ("confused", "smug", "shock"),
    "after": ("sad", "tired", "shock"),
    "check": ("smug", "laugh", "neutral"),
    "ok": ("smug", "laugh", "neutral"),
    "general": ("laugh", "shock", "confused", "smug"),
}

def _source_mood(path: Path, override: str = "auto") -> str:
    """Conservative tag: the source filename is an explicit human-provided clue.

    We NEVER pretend to identify a person's emotional state by appearance.
    'neutral' footage is used when there is no reliable editorial label.
    """
    if override != "auto":
        if override not in MOODS:
            raise ValueError("Unknown mood: " + override)
        return override
    name = " ".join(str(x).casefold() for x in path.parts[-3:])
    for mood, hints in MOOD_HINTS.items():
        if any(word in name for word in hints):
            return mood
    return "neutral"

def _index() -> list[dict]:
    """Cache 600 entries; confirm local existence and never trust catalog paths."""
    with LOCK:
        try:
            mtime = CATALOG.stat().st_mtime_ns
        except OSError:
            INDEX_CACHE.update(mtime=None, items=[])
            return []
        if INDEX_CACHE["mtime"] == mtime:
            return list(INDEX_CACHE["items"])
        try:
            content = json.loads(CATALOG.read_text(encoding="utf-8"))
            entries = content.get("frames", [])
            if not isinstance(entries, list):
                raise ValueError("Invalid frames catalog")
            filtered=[]
            for row in entries:
                if not isinstance(row, dict):
                    continue
                filename = row.get("file")
                key = row.get("id")
                if (not isinstance(filename, str)
                    or not filename.endswith(".jpg")
                    or not filename.startswith("frame_")
                    or Path(filename).name != filename
                    or not isinstance(key, str)
                    or len(key) != 20):
                    continue
                pic = BANK / filename
                if pic.is_file() and pic.stat().st_size > 1500:
                    filtered.append(dict(row))
            INDEX_CACHE.update(mtime=mtime, items=filtered)
            return list(filtered)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"Mellstroy catalog read failed: {type(exc).__name__}: {exc}", flush=True)
            INDEX_CACHE.update(mtime=None, items=[])
            return []

def status() -> dict:
    frames = _index()
    return {"count": len(frames), "target": TARGET,
            "missing": max(0, TARGET - len(frames)),
            "moods": dict(Counter(x.get("mood", "neutral") for x in frames)),
            "directory": str(BANK)}

def _dhash(image: Image.Image) -> int:
    small = image.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
    px = list(small.getdata())
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return value

def _sharp_enough(img: Image.Image) -> bool:
    if min(img.size) < 280:
        return False
    mini = img.convert("L").resize((320, 240), Image.Resampling.BILINEAR)
    stats = ImageStat.Stat(mini)
    if stats.mean[0] < 18 or stats.mean[0] > 243 or stats.stddev[0] < 10:
        return False
    edges = ImageStat.Stat(mini.filter(ImageFilter.FIND_EDGES)).stddev[0]
    return edges > 7

def _video_duration(video: Path) -> float:
    probe = shutil.which("ffprobe")
    if probe is None:
        raise RuntimeError("Install ffmpeg: sudo apt-get install -y ffmpeg")
    cmd = [probe, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(video)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    return max(1.0, float(done.stdout.strip()))

def _candidate_images(video: Path, temp: Path, remaining: int) -> list[Path]:
    binary = shutil.which("ffmpeg")
    if binary is None:
        raise RuntimeError("Install ffmpeg: sudo apt-get install -y ffmpeg")
    duration = _video_duration(video)
    # Oversample 4x so perceptual dedup can reject adjacent near-identical frames.
    desired = max(90, min(remaining * 4, 3200))
    fps = max(.15, min(3.0, desired / duration))
    pattern = temp / "frame_%07d.jpg"
    cmd = [binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-i", str(video),
           "-vf", f"fps={fps:.5f},scale='min(1000,iw)':-2",
           "-frames:v", str(desired),
           "-q:v", "4", str(pattern)]
    # Individual 2h video can be slow; explicit timeout avoids stuck deployments.
    subprocess.run(cmd, check=True, timeout=1500, capture_output=True)
    return sorted(temp.glob("frame_*.jpg"))

def _gather_inputs(path: Path) -> list[Path]:
    path = path.expanduser().resolve()
    if path.is_file():
        paths = [path]
    elif path.is_dir():
        paths = sorted(p for p in path.rglob("*")
                       if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE | ALLOWED_VIDEO)
    else:
        raise FileNotFoundError(f"No input media: {path}")
    if not paths:
        raise ValueError("No supported video/photo files found")
    if any(p.stat().st_size > MAX_SOURCE_BYTES for p in paths):
        raise ValueError("Input file over 750 MB. Split or compress the video.")
    return paths

def import_media(input_path: str | Path, *, target: int = TARGET,
                 mood: str = "auto", confirm_rights: bool = False,
                 source_credit: str = "permission-confirmed local media",
                 min_distance: int = 7) -> dict:
    """Extract *real* diverse frames, never draw/replace/distort a person's face.

    min_distance is an 8x8 dHash Hamming threshold for near-duplicates.
    Writes in-progress results incrementally, so a session can resume safely.
    """
    if not confirm_rights:
        raise PermissionError("Explicit --confirm-rights required for source media")
    if target < 1 or target > 3000:
        raise ValueError("Target must be 1..3000")
    if not 0 <= min_distance <= 24:
        raise ValueError("min_distance must be 0..24")
    inputs = _gather_inputs(Path(input_path))
    BANK.mkdir(parents=True, exist_ok=True)
    old = _index()
    seen_ids = {x["id"] for x in old}
    seen_hashes = []
    for x in old:
        try:
            seen_hashes.append(int(x["dhash"], 16))
        except (KeyError, TypeError, ValueError):
            pass
    added = 0
    rejected = 0
    errors = []
    for media in inputs:
        if len(old) >= target:
            break
        detected_mood = _source_mood(media, mood)
        try:
            with tempfile.TemporaryDirectory(prefix="mellframes_") as tmp:
                if media.suffix.lower() in ALLOWED_VIDEO:
                    candidates = _candidate_images(media, Path(tmp), target - len(old))
                else:
                    candidates = [media]
                for candidate in candidates:
                    if len(old) >= target:
                        break
                    try:
                        with Image.open(candidate) as opened:
                            if opened.width * opened.height > MAX_PIXELS:
                                raise ValueError("Image too large")
                            # EXIF rotation and aspect-preserving thumbnail only.
                            img = ImageOps.exif_transpose(opened).convert("RGB")
                            img.thumbnail((1100, 1100), Image.Resampling.LANCZOS)
                        if not _sharp_enough(img):
                            rejected += 1
                            continue
                        image_hash = _dhash(img)
                        if any((image_hash ^ h).bit_count() < min_distance for h in seen_hashes):
                            rejected += 1
                            continue
                        with tempfile.NamedTemporaryFile(suffix=".jpg",
                            prefix="clean_", dir=BANK, delete=False) as tmp_file:
                            temp_out = Path(tmp_file.name)
                        try:
                            img.save(temp_out, "JPEG", quality=89, optimize=True)
                            sha = hashlib.sha256(temp_out.read_bytes()).hexdigest()[:20]
                            if sha in seen_ids:
                                rejected += 1
                                continue
                            dest = BANK / f"frame_{sha}.jpg"
                            os.replace(temp_out, dest)
                            entry = {"id": sha, "file": dest.name, "mood": detected_mood,
                                     "dhash": f"{image_hash:016x}",
                                     "source": media.name, "credit": source_credit,
                                     "rights_confirmed": True,
                                     "uploaded_at": datetime.now(timezone.utc).isoformat(
                                         timespec="seconds")}
                            old.append(entry)
                            seen_hashes.append(image_hash)
                            seen_ids.add(sha)
                            added += 1
                        finally:
                            temp_out.unlink(missing_ok=True)
                    except (OSError, ValueError, UnidentifiedImageError) as exc:
                        rejected += 1
                        if len(errors) < 8:
                            errors.append(f"{candidate.name}: {type(exc).__name__}")
        except (OSError, ValueError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired, RuntimeError) as exc:
            if len(errors) < 8:
                errors.append(f"{media.name}: {type(exc).__name__}: {str(exc)[:120]}")
        # Commit atomically after *each* source, not only the last one.
        _save(old)
    _save(old)
    return {"added": added, "total": len(old), "target": target,
            "rejected": rejected, "sources": len(inputs), "errors": errors,
            "moods": dict(Counter(x["mood"] for x in old))}

def _save(items: list[dict]):
    BANK.mkdir(parents=True, exist_ok=True)
    tmp = CATALOG.with_suffix(".json.tmp")
    payload = {"version": 1, "target": TARGET, "frames": items}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CATALOG)
    with LOCK:
        INDEX_CACHE.update(mtime=None, items=[])

def templates(risk: str, seen: set[str] | None = None,
              limit: int = 18) -> list[dict]:
    """Choose relevant REAL source frames for each joke, favoring unused moods.

    The caller supplies previously used frame IDs; no past photo is selected
    again until the available pool has been exhausted for that user.
    """
    all_photos = _index()
    if not all_photos:
        return []
    seen = seen or set()
    expected = RISK_MOODS.get(risk, RISK_MOODS["general"])
    fresh = [p for p in all_photos if "mellstroy-" + p["id"] not in seen]
    pool = fresh if fresh else all_photos
    random.shuffle(pool)
    def rank(row):
        mood = row.get("mood", "neutral")
        return (0 if mood in expected else 1 if mood == "neutral" else 2,
                expected.index(mood) if mood in expected else 99)
    pool.sort(key=rank)
    selected = pool[:max(1, min(30, limit))]
    return [{"id": "mellstroy-" + x["id"],
             "name": "Mellstroy — original stream frame",
             "path": BANK / x["file"],
             "mood": x.get("mood", "neutral"),
             "risk": risk,
             "match_rank": rank(x)[0],
             "source_credit": x.get("credit", "Original footage")} for x in selected]

def contact_sheets(*, per_sheet: int = 60, cell: tuple[int, int] = (150, 125)) -> list[Path]:
    """Make compact contact sheets so the admin can review 600 genuine frames."""
    entries = _index()
    if not entries:
        return []
    from PIL import ImageDraw, ImageFont
    per_sheet = max(10, min(120, per_sheet))
    cols = 10
    rows = math.ceil(per_sheet / cols)
    output = []
    for page, start in enumerate(range(0, len(entries), per_sheet), 1):
        sheet = Image.new("RGB", (cols * cell[0], rows * cell[1]), "#141720")
        draw = ImageDraw.Draw(sheet)
        for i, entry in enumerate(entries[start:start + per_sheet]):
            x = (i % cols) * cell[0]
            y = (i // cols) * cell[1]
            try:
                with Image.open(BANK / entry["file"]) as source:
                    photo = ImageOps.fit(source.convert("RGB"), (cell[0], cell[1] - 23))
                sheet.paste(photo, (x, y))
                draw.rectangle((x, y + cell[1] - 23, x + cell[0], y + cell[1]), fill="#171923")
                draw.text((x + 5, y + cell[1] - 19),
                          f"{start + i + 1} {entry.get('mood', '?')[:8]}", fill="white")
            except (OSError, ValueError):
                continue
        path = BANK / f"contact_sheet_{page:02d}.jpg"
        sheet.save(path, "JPEG", quality=77)
        output.append(path)
    return output

def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="600 original Mellstroy frames from authorized videos")
    commands=parser.add_subparsers(dest="command", required=True)
    sub=commands.add_parser("import", help="Import photos/frames from local video(s)")
    sub.add_argument("source", type=Path)
    sub.add_argument("--target", type=int, default=TARGET)
    sub.add_argument("--mood", default="auto", choices=("auto",) + MOODS)
    sub.add_argument("--confirm-rights", action="store_true", required=False)
    sub.add_argument("--credit", default="Owner-authorized stream footage")
    sub.add_argument("--min-distance", type=int, default=7)
    commands.add_parser("status", help="Count unique frames imported")
    commands.add_parser("contact-sheet", help="Render contact sheets for reviewing frames")
    args=parser.parse_args(argv)
    if args.command == "import":
        print(json.dumps(import_media(args.source, target=args.target,
              mood=args.mood, confirm_rights=args.confirm_rights,
              source_credit=args.credit, min_distance=args.min_distance),
              ensure_ascii=False, indent=2))
    elif args.command == "contact-sheet":
        print("\n".join(map(str, contact_sheets())) or "No frames")
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
