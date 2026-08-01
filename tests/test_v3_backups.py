import sqlite3
import tempfile
import unittest
from pathlib import Path


class BackupTests(unittest.TestCase):
    def test_online_backup_has_checksum_and_restore_verifies_schema(self):
        from autoanime_v3.db.migrations import run_migrations
        from autoanime_v3.services.backups import BackupService

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "web.sqlite3"
            run_migrations(database)
            connection = sqlite3.connect(str(database))
            try:
                connection.execute(
                    "INSERT INTO app_settings(key, value_json, revision) VALUES ('marker', '1', 1)"
                )
                connection.commit()
            finally:
                connection.close()
            service = BackupService(database, root / "backups")
            record = service.create()
            self.assertTrue(Path(record.path).is_file())
            self.assertEqual(len(record.sha256), 64)

            connection = sqlite3.connect(str(database))
            try:
                connection.execute("UPDATE app_settings SET value_json = '2' WHERE key = 'marker'")
                connection.commit()
            finally:
                connection.close()
            service.restore(record.id, maintenance_mode=True)
            connection = sqlite3.connect(str(database))
            try:
                value = connection.execute(
                    "SELECT value_json FROM app_settings WHERE key = 'marker'"
                ).fetchone()[0]
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(value, "1")
            self.assertEqual(integrity, "ok")


if __name__ == "__main__":
    unittest.main()
