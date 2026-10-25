"""Atomic, process-safe JSON storage for meme review candidates (Ubuntu/Linux)."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "data" / "meme_review.json"
LOCK = ROOT / "data" / "meme_review.lock"


def read(index=INDEX):
    """Never silently overwrite a damaged or unparseable review index."""
    index = Path(index)
    if not index.exists():
        return []
    raw = index.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Legacy admin button once wrote the two literal bytes "\n" after JSON.
        # Recover ONLY this known valid-JSON prefix, preserving every record.
        if not raw.endswith("\\n"):
            raise
        payload = json.loads(raw[:-2])
    if not isinstance(payload, list):
        raise ValueError("Review index is not a JSON array")
    return payload


def transact(mutator, index=INDEX, lock=None):
    """Mutate under an inter-process flock and atomically replace the index.

    mutator receives a fresh list and may modify it in-place. Its return value
    is returned to the caller. Any exception leaves the original index intact.
    """
    index = Path(index)
    lock = Path(lock) if lock is not None else index.with_suffix(".lock")
    index.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            items = read(index)
            result = mutator(items)
            fd, name = tempfile.mkstemp(prefix=".memes-", suffix=".tmp", dir=index.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump(items, out, ensure_ascii=False, indent=2)
                    out.write("\n")
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(name, index)
            finally:
                Path(name).unlink(missing_ok=True)
            return result
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
