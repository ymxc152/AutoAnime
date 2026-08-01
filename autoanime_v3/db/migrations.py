"""Idempotent schema bootstrap for a new v3 Web console database."""

from pathlib import Path

from sqlalchemy import insert, select

from .engine import connect_sqlite, create_engine_for_path
from .schema import metadata, schema_migrations


SCHEMA_VERSION = 3


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
