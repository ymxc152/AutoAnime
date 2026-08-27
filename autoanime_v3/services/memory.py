"""Learned show aliases that do not bump RuleService content_hash / plan.rule_version."""

from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.normalize import alias_key, display_title
from autoanime_v3.parser import GENERIC_CONTEXT_KEYS


MEMORY_SOURCES = {"identify_batch", "review", "library_correction"}
GENERIC_ALIAS_KEYS = {alias_key(value) for value in GENERIC_CONTEXT_KEYS}


class ShowMemoryService:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)

    def load_overlay(self, connection=None):
        owns = connection is None
        if owns:
            connection = connect_sqlite(self.database_path)
            connection.row_factory = __import__("sqlite3").Row
        try:
            rows = connection.execute(
                "SELECT alias_key, canonical_title FROM learned_show_memory"
            ).fetchall()
        finally:
            if owns:
                connection.close()
        aliases = {}
        for row in rows:
            key = str(row["alias_key"] or "")
            title = display_title(str(row["canonical_title"] or ""))
            if key and title:
                aliases[key] = title
                aliases.setdefault(alias_key(title), title)
        return {"aliases": aliases}

    def remember(self, aliases, canonical_title, source="identify_batch", confidence=90, connection=None):
        title = display_title(str(canonical_title or ""))
        if not title:
            return 0
        if source not in MEMORY_SOURCES:
            source = "identify_batch"
        try:
            score = max(0, min(100, int(confidence)))
        except (TypeError, ValueError):
            score = 0
        keys = []
        seen = set()
        for raw in list(aliases or []) + [title]:
            key = alias_key(str(raw or ""))
            if not key or key in seen or key in GENERIC_ALIAS_KEYS:
                continue
            seen.add(key)
            keys.append(key)
        if not keys:
            return 0
        owns = connection is None
        if owns:
            connection = connect_sqlite(self.database_path)
        try:
            for key in keys:
                connection.execute(
                    """
                    INSERT INTO learned_show_memory(alias_key, canonical_title, source, confidence, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(alias_key) DO UPDATE SET
                        canonical_title = excluded.canonical_title,
                        source = excluded.source,
                        confidence = excluded.confidence,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, title, source, score),
                )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()
        return len(keys)

    def list(self):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            rows = connection.execute(
                """
                SELECT alias_key, canonical_title, source, confidence, updated_at
                FROM learned_show_memory
                ORDER BY updated_at DESC, alias_key
                """
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                "alias_key": str(row["alias_key"]),
                "canonical_title": str(row["canonical_title"]),
                "source": str(row["source"]),
                "confidence": int(row["confidence"] or 0),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        ]

    def remember_resolution(self, resolution, source="identify_batch", connection=None):
        if resolution is None or not getattr(resolution, "accepted", False):
            return 0
        title = display_title(str(resolution.canonical_title or ""))
        if not title:
            return 0
        aliases = [title]
        media = resolution.media
        if media is not None:
            aliases.append(media.path.name)
            aliases.append(media.path.stem)
            aliases.append(media.path.parent.name)
            aliases.append(media.context_name)
        for evidence in getattr(resolution, "evidence", None) or []:
            if getattr(evidence, "value", ""):
                aliases.append(evidence.value)
        confidence = int(round(float(getattr(resolution, "confidence", 0.0) or 0.0) * 100))
        return self.remember(aliases, title, source=source, confidence=confidence, connection=connection)
