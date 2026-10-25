"""Additive, non-destructive v2 storage on the existing tracker.db SQLite file."""
from __future__ import annotations

import calendar
from collections import Counter
from datetime import datetime, timezone, timedelta

IMPULSE_TAGS = {"more", "contacts", "ex", "money", "travel", "paranoia", "sleep"}
CHECKIN_TAGS = IMPULSE_TAGS | {"ok", "panic", "bad", "better", "worse"}


def now_utc():
    return datetime.now(timezone.utc)


def stamp(moment=None):
    return (moment or now_utc()).isoformat(timespec="seconds")


def migrate(connect):
    """No DROP, no altering user tables, compatible with historical sessions."""
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS v2_settings (
          user_id INTEGER PRIMARY KEY,
          last_substance TEXT NOT NULL DEFAULT 'Другое / не знаю',
          base_interval INTEGER NOT NULL DEFAULT 15,
          quiet INTEGER NOT NULL DEFAULT 0,
          boost_until TEXT
        );
        CREATE TABLE IF NOT EXISTS v2_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          session_id INTEGER,
          created_at TEXT NOT NULL,
          event_type TEXT NOT NULL,
          tag TEXT NOT NULL,
          resolved INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_v2_events_user_time ON v2_events(user_id, created_at);
        CREATE TABLE IF NOT EXISTS v2_followups (
          user_id INTEGER NOT NULL,
          kind TEXT NOT NULL,
          due_at TEXT NOT NULL,
          PRIMARY KEY(user_id, kind)
        );
        CREATE INDEX IF NOT EXISTS idx_v2_followups_due ON v2_followups(due_at);
        """)


def get_settings(c, uid):
    row = c.execute("SELECT * FROM v2_settings WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return {"last_substance": "Другое / не знаю", "base_interval": 15,
                "quiet": 0, "boost_until": None}
    return dict(row)


def set_setting(c, uid, **values):
    names = {"last_substance", "base_interval", "quiet", "boost_until"}
    if not values or not set(values) <= names:
        raise ValueError("Unknown settings")
    c.execute("INSERT INTO v2_settings(user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING", (uid,))
    parts = ", ".join(f"{name}=?" for name in values)
    c.execute(f"UPDATE v2_settings SET {parts} WHERE user_id=?",
              (*values.values(), uid))


def schedule_alert(c, uid, sid, minutes, now=None):
    """Explicitly turn on reminders for this session; never a silent background opt-in."""
    if minutes not in (10, 15, 30, 60):
        raise ValueError("Invalid reminder interval")
    due_at = stamp((now or now_utc()) + timedelta(minutes=minutes))
    c.execute(
        """INSERT INTO alerts(user_id,session_id,interval_min,next_at)
           VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
           session_id=excluded.session_id,
           interval_min=excluded.interval_min,
           next_at=excluded.next_at""",
        (uid, sid, minutes, due_at),
    )
    return due_at


def active_minutes(settings, now=None):
    base = int(settings["base_interval"])
    if base not in (10, 15, 30, 60):
        base = 15
    if settings["quiet"]:
        return 0
    if settings["boost_until"]:
        try:
            if datetime.fromisoformat(settings["boost_until"]) > (now or now_utc()):
                return min(10, base)
        except (ValueError, TypeError):
            pass
    return base


def record_event(c, uid, session_id, kind, tag, when=None):
    if kind not in ("risk", "state", "followup") or tag not in CHECKIN_TAGS:
        raise ValueError("Invalid event")
    c.execute(
        "INSERT INTO v2_events(user_id,session_id,created_at,event_type,tag) VALUES(?,?,?,?,?)",
        (uid, session_id, stamp(when), kind, tag)
    )


def resolve_recent(c, uid, when=None):
    since = stamp((when or now_utc()) - timedelta(hours=24))
    row = c.execute(
        "SELECT id FROM v2_events WHERE user_id=? AND event_type='risk' "
        "AND resolved=0 AND created_at>=? ORDER BY id DESC LIMIT 1", (uid, since)
    ).fetchone()
    if row:
        c.execute("UPDATE v2_events SET resolved=1 WHERE id=?", (row["id"],))
        return True
    return False


def schedule_followup(c, uid, kind, minutes=10, now=None):
    if kind not in ("urge", "wellbeing", "after"):
        raise ValueError("Invalid follow-up kind")
    if (kind == "after" and minutes != 480) or (kind != "after" and minutes not in (10, 15)):
        raise ValueError("Unsupported follow-up delay")
    due = stamp((now or now_utc()) + timedelta(minutes=minutes))
    c.execute("""INSERT INTO v2_followups(user_id,kind,due_at) VALUES(?,?,?)
              ON CONFLICT(user_id,kind) DO UPDATE SET due_at=excluded.due_at""",
              (uid, kind, due))
    return due


def claim_due_followups(connect, now=None, limit=30):
    """Claim atomically; avoids repeated sends when two scheduler ticks overlap."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute(
            "SELECT user_id, kind FROM v2_followups WHERE due_at<=? ORDER BY due_at LIMIT ?",
            (stamp(now), limit)
        ).fetchall()
        for item in rows:
            c.execute("DELETE FROM v2_followups WHERE user_id=? AND kind=?",
                      (item["user_id"], item["kind"]))
        return [dict(row) for row in rows]


def erase_user(c, uid):
    c.execute("DELETE FROM v2_followups WHERE user_id=?", (uid,))
    c.execute("DELETE FROM v2_events WHERE user_id=?", (uid,))
    c.execute("DELETE FROM v2_settings WHERE user_id=?", (uid,))


def monthly_summary(c, uid, month=None):
    """Only observed self-reported events; never infer measured drug consumption."""
    now = month or now_utc()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if first.month == 12:
        end = first.replace(year=first.year + 1, month=1)
    else:
        end = first.replace(month=first.month + 1)
    rows = c.execute(
        "SELECT id,substance,started,ended FROM sessions "
        "WHERE user_id=? AND started>=? AND started<? ORDER BY started",
        (uid, stamp(first), stamp(end))
    ).fetchall()
    events = c.execute(
        "SELECT event_type,tag,resolved,created_at FROM v2_events "
        "WHERE user_id=? AND created_at>=? AND created_at<?",
        (uid, stamp(first), stamp(end))
    ).fetchall()
    days = [0] * calendar.monthrange(first.year, first.month)[1]
    substance = Counter()
    weekdays = [0] * 7
    hours = [0] * 24
    durations = []
    for row in rows:
        try:
            dt = datetime.fromisoformat(row["started"])
            if 1 <= dt.day <= len(days):
                days[dt.day - 1] += 1
                weekdays[dt.weekday()] += 1
                hours[dt.hour] += 1
            substance[row["substance"]] += 1
            if row["ended"]:
                hours_long = (datetime.fromisoformat(row["ended"]) - dt).total_seconds() / 3600
                if 0 <= hours_long <= 24 * 30:
                    durations.append(hours_long)
        except (TypeError, ValueError):
            continue
    risks = Counter(x["tag"] for x in events if x["event_type"] == "risk")
    resolved = sum(1 for x in events if x["event_type"] == "risk" and x["resolved"])
    checkins = sum(1 for x in events if x["event_type"] == "state")
    return {
        "month": first.strftime("%m.%Y"), "episodes": len(rows),
        "days": days, "substances": substance, "risks": risks,
        "resolved": resolved, "checkins": checkins,
        "weekdays": weekdays, "hours": hours,
        "durations": durations, "event_count": len(events),
    }
