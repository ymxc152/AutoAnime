"""Idempotent schema bootstrap for a new v3 Web console database."""

import json
from pathlib import Path

from sqlalchemy import insert, select

from .engine import connect_sqlite, create_engine_for_path
from .profile_snapshots import build_profile_snapshot, encode_profile_snapshot
from .schema import metadata, schema_migrations


SCHEMA_VERSION = 6


def connect_database(database_path):
    return connect_sqlite(database_path)


def run_migrations(database_path):
    path = Path(database_path).resolve()
    engine = create_engine_for_path(path)
    try:
        metadata.create_all(engine)
        with engine.begin() as connection:
            schedule_columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(schedules)").fetchall()
            }
            if "revision" not in schedule_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE schedules ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
                )
            webhook_columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(webhook_sources)").fetchall()
            }
            if "revision" not in webhook_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE webhook_sources ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
                )
            plan_item_columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(plan_items)").fetchall()
            }
            plan_item_alters = {
                "decision": "ALTER TABLE plan_items ADD COLUMN decision VARCHAR(16)",
                "reject_reason": "ALTER TABLE plan_items ADD COLUMN reject_reason TEXT",
                "decided_by": "ALTER TABLE plan_items ADD COLUMN decided_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
                "decided_at": "ALTER TABLE plan_items ADD COLUMN decided_at VARCHAR(32)",
            }
            for column, statement in plan_item_alters.items():
                if column not in plan_item_columns:
                    connection.exec_driver_sql(statement)
            profile_columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(scan_profiles)").fetchall()
            }
            profile_alters = {
                "deleted_at": "ALTER TABLE scan_profiles ADD COLUMN deleted_at VARCHAR(32)",
                "deleted_snapshot_json": "ALTER TABLE scan_profiles ADD COLUMN deleted_snapshot_json TEXT",
            }
            for column, statement in profile_alters.items():
                if column not in profile_columns:
                    connection.exec_driver_sql(statement)
            history_alters = {
                "scan_runs": "ALTER TABLE scan_runs ADD COLUMN profile_snapshot_json TEXT NOT NULL DEFAULT '{}'",
                "plans": "ALTER TABLE plans ADD COLUMN profile_snapshot_json TEXT NOT NULL DEFAULT '{}'",
            }
            for table, statement in history_alters.items():
                columns = {
                    row[1]
                    for row in connection.exec_driver_sql("PRAGMA table_info(%s)" % table).fetchall()
                }
                if "profile_snapshot_json" not in columns:
                    connection.exec_driver_sql(statement)
            for profile in connection.exec_driver_sql("SELECT * FROM scan_profiles").mappings():
                if profile["deleted_at"] is not None and not profile["deleted_snapshot_json"]:
                    snapshot = build_profile_snapshot(
                        connection,
                        profile["id"],
                        profile_row=profile,
                        snapshot_at=profile["updated_at"],
                    )
                    connection.exec_driver_sql(
                        "UPDATE scan_profiles SET deleted_snapshot_json = ? WHERE id = ? AND deleted_snapshot_json IS NULL",
                        (encode_profile_snapshot(snapshot), profile["id"]),
                    )
            for run in connection.exec_driver_sql(
                "SELECT id, profile_id, profile_snapshot_json, started_at FROM scan_runs"
            ).mappings():
                if not run["profile_snapshot_json"] or run["profile_snapshot_json"] == "{}":
                    snapshot = build_profile_snapshot(connection, run["profile_id"], snapshot_at=run["started_at"])
                    connection.exec_driver_sql(
                        "UPDATE scan_runs SET profile_snapshot_json = ? WHERE id = ? AND (profile_snapshot_json IS NULL OR profile_snapshot_json = '{}')",
                        (encode_profile_snapshot(snapshot), run["id"]),
                    )
            for plan in connection.exec_driver_sql(
                "SELECT id, scan_run_id, profile_id, profile_snapshot_json, created_at FROM plans"
            ).mappings():
                if not plan["profile_snapshot_json"] or plan["profile_snapshot_json"] == "{}":
                    run_snapshot = connection.exec_driver_sql(
                        "SELECT profile_snapshot_json FROM scan_runs WHERE id = ?",
                        (plan["scan_run_id"],),
                    ).scalar_one_or_none()
                    snapshot_json = run_snapshot if run_snapshot and run_snapshot != "{}" else encode_profile_snapshot(
                        build_profile_snapshot(connection, plan["profile_id"], snapshot_at=plan["created_at"])
                    )
                    connection.exec_driver_sql(
                        "UPDATE plans SET profile_snapshot_json = ? WHERE id = ? AND (profile_snapshot_json IS NULL OR profile_snapshot_json = '{}')",
                        (snapshot_json, plan["id"]),
                    )
            connection.exec_driver_sql(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_sessions_open
                ON agent_sessions(kind, target_id) WHERE status = 'open'
                """
            )
            existing = connection.execute(
                select(schema_migrations.c.version).where(
                    schema_migrations.c.version == SCHEMA_VERSION
                )
            ).scalar_one_or_none()
            if existing is None:
                connection.execute(insert(schema_migrations).values(version=SCHEMA_VERSION))
    finally:
        engine.dispose()
    return path
