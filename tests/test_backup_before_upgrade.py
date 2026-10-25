"""Test real SQLite online backup without touching the VPS database."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import backup_before_upgrade


class BackupTests(unittest.TestCase):
    def test_backup_retains_rows_and_does_not_delete_old_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "tracker.db"
            backups = Path(tmp) / "backups"
            with sqlite3.connect(source) as c:
                c.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
                c.execute("INSERT INTO sample(value) VALUES ('keep me')")
            backups.mkdir()
            old = backups / "older.db"
            old.write_bytes(b"previous snapshot")
            dest = backup_before_upgrade.backup(source, backups)
            self.assertTrue(dest.is_file())
            self.assertEqual(old.read_bytes(), b"previous snapshot")
            with sqlite3.connect(dest) as copied:
                self.assertEqual(copied.execute("SELECT value FROM sample").fetchone()[0], "keep me")
                self.assertEqual(copied.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
