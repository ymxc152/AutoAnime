"""Revisioned non-secret application settings."""

import json
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import RevisionConflictError, ValidationError
from autoanime_v3.services.auth import AUTH_LOCAL_BYPASS_KEY, LOCAL_HOOK_TRUST_KEY


DEFAULT_SETTINGS = {
    AUTH_LOCAL_BYPASS_KEY: True,
    LOCAL_HOOK_TRUST_KEY: True,
}

BOOLEAN_SETTINGS = {AUTH_LOCAL_BYPASS_KEY, LOCAL_HOOK_TRUST_KEY}


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
        self.ensure_defaults()

    def ensure_defaults(self):
        with SqliteUnitOfWork(self.database_path) as uow:
            changed = False
            for key, value in DEFAULT_SETTINGS.items():
                row = uow.connection.execute(
                    "SELECT 1 FROM app_settings WHERE key = ?", (key,)
                ).fetchone()
                if row is None:
                    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    uow.connection.execute(
                        "INSERT INTO app_settings(key, value_json, revision) VALUES (?, ?, 1)",
                        (key, encoded),
                    )
                    changed = True
            if changed:
                uow.commit()

    def list(self):
        self.ensure_defaults()
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            return [setting_view(row) for row in connection.execute("SELECT * FROM app_settings ORDER BY key")]
        finally:
            connection.close()

    def get(self, key, default=None):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            row = connection.execute("SELECT * FROM app_settings WHERE key = ?", (key,)).fetchone()
        finally:
            connection.close()
        if row is None:
            return DEFAULT_SETTINGS.get(key, default)
        return json.loads(row["value_json"])

    def update(self, key, value, revision):
        normalized_key = str(key).strip()
        if not normalized_key:
            raise ValidationError("Setting key cannot be empty")
        if normalized_key in BOOLEAN_SETTINGS and type(value) is not bool:
            raise ValidationError("Setting value must be a boolean", {"key": normalized_key})
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
