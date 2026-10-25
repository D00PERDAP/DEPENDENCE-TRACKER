"""Consistent SQLite backup before bot upgrades. No deletion or rotation."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "tracker.db"
BACKUPS = ROOT / "backups"


def backup(source=DB, directory=BACKUPS):
    source = Path(source)
    directory = Path(directory)
    if not source.is_file():
        print("No database yet; no backup needed.")
        return None
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("tracker-before-v2-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".db")
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=20) as original:
            with sqlite3.connect(target, timeout=20) as copied:
                original.backup(copied)
                status = copied.execute("PRAGMA quick_check").fetchone()[0]
                if status != "ok":
                    raise RuntimeError("Backup failed database integrity check")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    print("Database backup saved:", target)
    return target


if __name__ == "__main__":
    backup()
