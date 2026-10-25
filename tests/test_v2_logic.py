"""Regression tests for redesigned Telegram UX and health/safety/stats logic."""
import io
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import bot
import context_memes
import harm_reduction
import stats_dashboard
import v2_data
import v2_experience


class V2DataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(bot, "DB", Path(self.tmp.name) / "tracker.db")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        bot.init()
        self.at = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)

    def test_additive_migration_preserves_existing_data(self):
        with bot.connect() as c:
            c.execute("INSERT INTO users(user_id,substance) VALUES(42,'Мефедрон')")
            c.execute("INSERT INTO checklist(user_id,title,selected) VALUES(42,'Не звонить',1)")
        bot.init()
        with bot.connect() as c:
            self.assertEqual(c.execute("SELECT substance FROM users WHERE user_id=42").fetchone()[0],
                             "Мефедрон")
            self.assertEqual(c.execute("SELECT title FROM checklist WHERE user_id=42").fetchone()[0],
                             "Не звонить")
            db_tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"v2_events", "v2_followups", "v2_settings"} <= db_tables)

    def test_intervals_boost_and_quiet(self):
        with bot.connect() as c:
            db.set_setting(c, 42, base_interval=30)
            s = db.get_settings(c, 42)
            self.assertEqual(db.active_minutes(s, self.at), 30)
            db.set_setting(c, 42, boost_until=v2_data.stamp(self.at + timedelta(hours=1)))
            s = db.get_settings(c, 42)
            self.assertEqual(db.active_minutes(s, self.at), 10)
            self.assertEqual(db.active_minutes(s, self.at + timedelta(hours=2)), 30)
            db.set_setting(c, 42, quiet=1)
            self.assertEqual(db.active_minutes(db.get_settings(c, 42), self.at), 0)

    def test_followup_is_optin_deduplicated_and_claimed_once(self):
        with bot.connect() as c:
            db.schedule_followup(c, 42, "urge", 10, self.at)
            db.schedule_followup(c, 42, "urge", 10, self.at)
            db.schedule_followup(c, 99, "wellbeing", 10, self.at)
        self.assertEqual(db.claim_due_followups(bot.connect, self.at + timedelta(minutes=9)), [])
        claimed = db.claim_due_followups(bot.connect, self.at + timedelta(minutes=11))
        self.assertEqual(len(claimed), 2)
        self.assertEqual(db.claim_due_followups(bot.connect, self.at + timedelta(minutes=11)), [])

    def test_monthly_graph_data_and_user_erasure(self):
        with bot.connect() as c:
            c.execute("INSERT INTO sessions(user_id,substance,started,ended) VALUES(42,'Мефедрон',?,?)",
                      (db.stamp(self.at), db.stamp(self.at + timedelta(hours=2))))
            c.execute("INSERT INTO sessions(user_id,substance,started) VALUES(42,'Алкоголь',?)",
                      (db.stamp(self.at + timedelta(days=1)),))
            db.record_event(c, 42, None, "risk", "ex", self.at)
            db.record_event(c, 42, None, "risk", "more", self.at)
            self.assertTrue(db.resolve_recent(c, 42, self.at))
            db.record_event(c, 42, None, "state", "ok", self.at)
            db.schedule_followup(c, 42, "urge", 10, self.at)
        with bot.connect() as c:
            summary = db.monthly_summary(c, 42, self.at)
        self.assertEqual(summary["episodes"], 2)
        self.assertEqual(summary["days"][24], 1)
        self.assertEqual(summary["days"][25], 1)
        self.assertEqual(summary["resolved"], 1)
        self.assertEqual(summary["risks"]["ex"], 1)
        self.assertEqual(summary["checkins"], 1)
        jpeg = stats_dashboard.make_dashboard(summary)
        with Image.open(io.BytesIO(jpeg)) as image:
            self.assertEqual(image.size, (1100, 1470))
            self.assertEqual(image.format, "JPEG")
        with bot.connect() as c:
            db.erase_user(c, 42)
            self.assertEqual(c.execute("SELECT count(*) FROM v2_events WHERE user_id=42").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM v2_followups WHERE user_id=42").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM sessions WHERE user_id=42").fetchone()[0], 2)

    def test_empty_dashboard_not_broken(self):
        with bot.connect() as c:
            summary = db.monthly_summary(c, 42, self.at)
        self.assertEqual(summary["episodes"], 0)
        self.assertTrue(stats_dashboard.make_dashboard(summary).startswith(b"\xff\xd8"))


class V2SafetyTests(unittest.TestCase):
    def test_emergency_symptoms_never_show_meme(self):
        for substance in harm_reduction.SUBSTANCES:
            for tag in ("heart", "breath", "heat", "overdose", "unknown"):
                card = harm_reduction.get_card(substance, tag)
                self.assertTrue(card.urgent)
                self.assertIn("скор", card.text.lower())
                self.assertNotIn("🎭", card.text)

    def test_substance_specific_guidance_and_no_dosing(self):
        panic = harm_reduction.get_card("Мефедрон", "panic")
        self.assertFalse(panic.urgent)
        self.assertIn("стимулятор", panic.text.lower())
        mdma = harm_reduction.get_card("MDMA", "nausea")
        self.assertIn("литрами", mdma.text)
        self.assertIn("налоксон", harm_reduction.get_card("Опиоиды", "overdose").text.lower())

    def test_risk_routing(self):
        self.assertEqual(v2_experience.risk_from_titles(["Не звонить бывшей"]), "ex")
        self.assertEqual(v2_experience.risk_from_titles(["Не тратить деньги"]), "money")
        self.assertEqual(v2_experience.risk_from_titles(["Не покупать ещё"]), "more")
        self.assertIn("10 минут", harm_reduction.impulse_message("more", "Мефедрон"))


class V2MemesTests(unittest.TestCase):
    def test_only_contextual_approved_photos_and_no_old_quote_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            specific = root / "contextual" / "meph" / "ex"
            specific.mkdir(parents=True)
            (specific / "relevant.jpg").write_bytes(b"test-verified")
            (specific / "original_badcard.jpg").write_bytes(b"do-not-show")
            randoms = root / "general"
            randoms.mkdir()
            (randoms / "other.jpg").write_bytes(b"generic")
            found = context_memes.candidates("Мефедрон", "ex", root)
            self.assertEqual([x.name for x in found], ["relevant.jpg"])
            self.assertEqual([x.name for x in context_memes.candidates("Кетамин", "ex", root)],
                             ["other.jpg"])
            self.assertIn("Меф", context_memes.joke("Мефедрон", "general"))


if __name__ == "__main__":
    unittest.main()
