"""Revisioned non-secret application settings."""

import json
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import RevisionConflictError, ValidationError
from autoanime_v3.services.auth import AUTH_LOCAL_BYPASS_KEY, LOCAL_HOOK_TRUST_KEY


OPENAI_ENABLED_KEY = "openai.enabled"
OPENAI_BASE_URL_KEY = "openai.base_url"
OPENAI_MODEL_KEY = "openai.model"
OPENAI_TIMEOUT_KEY = "openai.timeout"
OPENAI_API_KEY_SECRET = "openai.api_key"

REVIEW_ENABLED_KEY = "review.enabled"

PARSE_AGENT_MODE_KEY = "parse.agent_mode"

METADATA_BANGUMI_ENABLED_KEY = "metadata.bangumi_enabled"
METADATA_TMDB_ENABLED_KEY = "metadata.tmdb_enabled"
METADATA_TIMEOUT_KEY = "metadata.timeout"
METADATA_TMDB_API_KEY_SECRET = "metadata.tmdb_api_key"

DEFAULT_SETTINGS = {
    AUTH_LOCAL_BYPASS_KEY: True,
    LOCAL_HOOK_TRUST_KEY: True,
    OPENAI_ENABLED_KEY: False,
    OPENAI_BASE_URL_KEY: "https://api.openai.com",
    OPENAI_MODEL_KEY: "gpt-4.1-mini",
    OPENAI_TIMEOUT_KEY: 30,
    METADATA_BANGUMI_ENABLED_KEY: False,
    METADATA_TMDB_ENABLED_KEY: False,
    METADATA_TIMEOUT_KEY: 12,
    REVIEW_ENABLED_KEY: False,
    PARSE_AGENT_MODE_KEY: "off",
}

BOOLEAN_SETTINGS = {
    AUTH_LOCAL_BYPASS_KEY,
    LOCAL_HOOK_TRUST_KEY,
    OPENAI_ENABLED_KEY,
    METADATA_BANGUMI_ENABLED_KEY,
    METADATA_TMDB_ENABLED_KEY,
    REVIEW_ENABLED_KEY,
}
INTEGER_SETTINGS = {OPENAI_TIMEOUT_KEY, METADATA_TIMEOUT_KEY}
STRING_SETTINGS = {OPENAI_BASE_URL_KEY, OPENAI_MODEL_KEY, PARSE_AGENT_MODE_KEY}


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

    def openai_public_view(self, secret_configured=False):
        """Safe AI settings block for the WebUI (never includes the API key)."""
        items = {item["key"]: item for item in self.list()}

        def pack(key, default):
            row = items.get(key)
            if row is None:
                return default, 0
            return row["value"], int(row["revision"])

        enabled, enabled_rev = pack(OPENAI_ENABLED_KEY, False)
        base_url, base_rev = pack(OPENAI_BASE_URL_KEY, "https://api.openai.com")
        model, model_rev = pack(OPENAI_MODEL_KEY, "gpt-4.1-mini")
        timeout, timeout_rev = pack(OPENAI_TIMEOUT_KEY, 30)
        review_enabled, review_rev = pack(REVIEW_ENABLED_KEY, False)
        parse_mode, parse_rev = pack(PARSE_AGENT_MODE_KEY, "off")
        return {
            "enabled": bool(enabled),
            "enabled_revision": enabled_rev,
            "base_url": str(base_url or "https://api.openai.com"),
            "base_url_revision": base_rev,
            "model": str(model or "gpt-4.1-mini"),
            "model_revision": model_rev,
            "timeout": int(timeout or 30),
            "timeout_revision": timeout_rev,
            "api_key_configured": bool(secret_configured),
            "ready": bool(enabled) and bool(secret_configured),
            "review_enabled": bool(review_enabled),
            "review_enabled_revision": review_rev,
            "parse_agent_mode": str(parse_mode or "off"),
            "parse_agent_mode_revision": parse_rev,
        }

    def metadata_public_view(self, secret_configured=False):
        """Safe metadata-provider settings block for the WebUI (never includes the API key)."""
        items = {item["key"]: item for item in self.list()}

        def pack(key, default):
            row = items.get(key)
            if row is None:
                return default, 0
            return row["value"], int(row["revision"])

        bangumi_enabled, bangumi_rev = pack(METADATA_BANGUMI_ENABLED_KEY, False)
        tmdb_enabled, tmdb_rev = pack(METADATA_TMDB_ENABLED_KEY, False)
        timeout, timeout_rev = pack(METADATA_TIMEOUT_KEY, 12)
        return {
            "bangumi_enabled": bool(bangumi_enabled),
            "bangumi_enabled_revision": bangumi_rev,
            "tmdb_enabled": bool(tmdb_enabled),
            "tmdb_enabled_revision": tmdb_rev,
            "timeout": int(timeout or 12),
            "timeout_revision": timeout_rev,
            "tmdb_api_key_configured": bool(secret_configured),
            "ready": bool(bangumi_enabled or (tmdb_enabled and secret_configured)),
        }

    def update(self, key, value, revision):
        normalized_key = str(key).strip()
        if not normalized_key:
            raise ValidationError("Setting key cannot be empty")
        if normalized_key in BOOLEAN_SETTINGS and type(value) is not bool:
            raise ValidationError("Setting value must be a boolean", {"key": normalized_key})
        if normalized_key in INTEGER_SETTINGS:
            try:
                value = int(value)
            except (TypeError, ValueError) as error:
                raise ValidationError("Setting value must be an integer", {"key": normalized_key}) from error
            if normalized_key == OPENAI_TIMEOUT_KEY and value < 5:
                raise ValidationError("OpenAI timeout must be at least 5 seconds", {"key": normalized_key})
            if normalized_key == METADATA_TIMEOUT_KEY and value < 2:
                raise ValidationError("Metadata timeout must be at least 2 seconds", {"key": normalized_key})
        if normalized_key in STRING_SETTINGS:
            if not isinstance(value, str) or not value.strip():
                raise ValidationError("Setting value must be a non-empty string", {"key": normalized_key})
            value = value.strip()
            if normalized_key == OPENAI_BASE_URL_KEY and not (
                value.startswith("http://") or value.startswith("https://")
            ):
                raise ValidationError(
                    "OpenAI base URL must start with http:// or https://",
                    {"key": normalized_key},
                )
            if normalized_key == PARSE_AGENT_MODE_KEY and value not in {"off", "uncertain", "all"}:
                raise ValidationError(
                    "parse.agent_mode must be one of off / uncertain / all",
                    {"key": normalized_key},
                )
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
                        {
                            "expected_revision": int(revision),
                            "actual_revision": int(row["revision"]),
                        },
                    )
            result = setting_view(
                uow.connection.execute(
                    "SELECT * FROM app_settings WHERE key = ?", (normalized_key,)
                ).fetchone()
            )
            uow.commit()
            return result
