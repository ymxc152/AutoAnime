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
            "learned_show_memory",
            "agent_sessions",
            "agent_messages",
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
        self.assertEqual(rows, [(6,)])

    def test_logical_delete_and_history_snapshot_columns_are_migrated(self):
        migrations = self.migration_module()
        migrations.run_migrations(self.database)

        connection = sqlite3.connect(str(self.database))
        try:
            columns = {
                table: {row[1] for row in connection.execute("PRAGMA table_info(%s)" % table)}
                for table in ("scan_profiles", "scan_runs", "plans")
            }
        finally:
            connection.close()

        self.assertTrue(
            {"deleted_at", "deleted_snapshot_json"}.issubset(columns["scan_profiles"])
        )
        self.assertIn("profile_snapshot_json", columns["scan_runs"])
        self.assertIn("profile_snapshot_json", columns["plans"])

    def test_migration_does_not_write_delete_snapshot_for_active_profiles(self):
        from autoanime_v3.db import migrations

        engine = migrations.create_engine_for_path(self.database)
        try:
            migrations.metadata.create_all(engine)
        finally:
            engine.dispose()

        connection = sqlite3.connect(str(self.database))
        try:
            connection.execute(
                """
                INSERT INTO storage_roots(kind, path, normalized_path)
                VALUES ('source', 'C:/source', 'c:/source')
                """
            )
            connection.execute(
                """
                INSERT INTO storage_roots(kind, path, normalized_path)
                VALUES ('library', 'C:/library', 'c:/library')
                """
            )
            connection.execute(
                """
                INSERT INTO scan_profiles(
                    name, source_root_id, library_root_id, mode, execution_policy,
                    min_confidence, stability_seconds, watch_enabled, enabled, revision
                ) VALUES ('active', 1, 2, 'link', 'review_all', 80, 30, 0, 1, 1)
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrations.run_migrations(self.database)

        connection = sqlite3.connect(str(self.database))
        try:
            snapshot = connection.execute(
                "SELECT deleted_snapshot_json FROM scan_profiles WHERE name = 'active'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIsNone(snapshot)

    def test_migration_adds_plan_item_decision_columns_to_existing_table(self):
        migrations = self.migration_module()
        connection = sqlite3.connect(str(self.database))
        try:
            connection.execute(
                """
                CREATE TABLE plan_items(
                    id INTEGER PRIMARY KEY,
                    execution_status VARCHAR(32) NOT NULL DEFAULT 'pending'
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrations.run_migrations(self.database)
        migrations.run_migrations(self.database)

        connection = sqlite3.connect(str(self.database))
        try:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(plan_items)").fetchall()
            }
        finally:
            connection.close()
        self.assertTrue(
            {"decision", "reject_reason", "decided_by", "decided_at"}.issubset(columns)
        )

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

    def test_agent_session_tables_have_columns_and_unique_open_index(self):
        migrations = self.migration_module()
        migrations.run_migrations(self.database)
        connection = sqlite3.connect(str(self.database))
        try:
            session_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(agent_sessions)").fetchall()
            }
            message_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(agent_messages)").fetchall()
            }
            index_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'uq_agent_sessions_open'"
            ).fetchone()
        finally:
            connection.close()
        self.assertTrue(
            {"id", "kind", "target_id", "status", "created_at", "updated_at"}.issubset(session_columns)
        )
        self.assertTrue(
            {"id", "session_id", "role", "content", "proposal_json", "created_at"}.issubset(message_columns)
        )
        self.assertIsNotNone(index_sql)
        self.assertIn("kind", index_sql[0])
        self.assertIn("target_id", index_sql[0])
        self.assertIn("status = 'open'", index_sql[0].replace('"', "'"))


if __name__ == "__main__":
    unittest.main()
