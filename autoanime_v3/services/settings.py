"""Revisioned non-secret application settings."""

import json
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import RevisionConflictError, ValidationError


def setting_view(row):
    return {
        "key": str(row["key"]),
        "value": json.loads(row["value_json"]),
        "revision": int(row["revision"]),
        "updated_at": str(row["updated_at"]),
    }


class SettingsService:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)

    def list(self):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            return [setting_view(row) for row in connection.execute("SELECT * FROM app_settings ORDER BY key")]
        finally:
            connection.close()

    def update(self, key, value, revision):
        normalized_key = str(key).strip()
        if not normalized_key:
            raise ValidationError("Setting key cannot be empty")
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with SqliteUnitOfWork(self.database_path) as uow:
            row = uow.connection.execute(
                "SELECT * FROM app_settings WHERE key = ?", (normalized_key,)
            ).fetchone()
            if row is None:
                if int(revision) != 0:
                    raise RevisionConflictError(
                        "Setting does not exist at the requested revision",
                        {"expected_revision": int(revision), "actual_revision": 0},
                    )
                uow.connection.execute(
                    "INSERT INTO app_settings(key, value_json, revision) VALUES (?, ?, 1)",
                    (normalized_key, encoded),
                )
            else:
                updated = uow.connection.execute(
                    """
                    UPDATE app_settings
                    SET value_json = ?, revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE key = ? AND revision = ?
                    """,
                    (encoded, normalized_key, int(revision)),
                ).rowcount
                if updated != 1:
                    raise RevisionConflictError(
                        "Setting was changed by another request",
                        {"expected_revision": int(revision), "actual_revision": int(row["revision"])},
                    )
            result = setting_view(
                uow.connection.execute(
                    "SELECT * FROM app_settings WHERE key = ?", (normalized_key,)
                ).fetchone()
            )
            uow.commit()
            return result
