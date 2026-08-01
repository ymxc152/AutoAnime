import sqlite3
import tempfile
import unittest
from pathlib import Path


class WebSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "library.sqlite3"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def migration_module(self):
        try:
            from autoanime_v3.db import migrations
        except ModuleNotFoundError as error:
            self.fail("Web schema migrations are not implemented: %s" % error)
        return migrations

    def table_names(self):
        connection = sqlite3.connect(str(self.database))
        try:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            connection.close()

    def test_migration_creates_complete_web_console_schema(self):
        migrations = self.migration_module()

        migrations.run_migrations(self.database)

        expected = {
            "schema_migrations",
            "users",
            "user_sessions",
            "app_settings",
            "secret_settings",
            "audit_events",
            "storage_roots",
            "scan_profiles",
            "profile_rules",
            "schedules",
            "webhook_sources",
            "resource_leases",
            "shows",
            "seasons",
            "episodes",
            "media_files",
            "file_locations",
            "media_assignments",
            "identification_results",
            "identification_evidence",
            "metadata_records",
            "jobs",
            "job_events",
            "scan_runs",
            "scan_items",
            "review_items",
            "plans",
            "plan_items",
            "operation_batches",
            "operation_items",
            "change_requests",
            "rule_sets",
            "rule_revisions",
            "backup_records",
        }
        self.assertTrue(expected.issubset(self.table_names()))

    def test_migration_is_idempotent_and_records_schema_version(self):
        migrations = self.migration_module()

        migrations.run_migrations(self.database)
        migrations.run_migrations(self.database)

        connection = sqlite3.connect(str(self.database))
        try:
            rows = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(rows, [(3,)])

    def test_database_connections_enable_foreign_keys_wal_and_busy_timeout(self):
        migrations = self.migration_module()

        migrations.run_migrations(self.database)
        connection = migrations.connect_database(self.database)
        try:
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(foreign_keys, 1)
        self.assertEqual(str(journal_mode).casefold(), "wal")
        self.assertGreaterEqual(int(busy_timeout), 5000)


if __name__ == "__main__":
    unittest.main()
