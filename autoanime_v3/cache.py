from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Optional, Union

from . import PARSER_VERSION
from .models import Evidence, MediaFile, Resolution
from .normalize import alias_key


SCHEMA_VERSION = 2


def _source_key(path: Union[Path, str]) -> str:
    """Return a stable physical-source key using the host platform's path rules."""
    return os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))


def fingerprint(media: MediaFile, decision_version: str = "") -> str:
    value = "\0".join(
        [
            PARSER_VERSION,
            decision_version,
            str(media.path).casefold(),
            media.relative_path.casefold(),
            media.path.name,
            media.context_name,
            str(media.size),
            str(media.mtime_ns),
        ]
    )
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


class ResolutionCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: Optional[sqlite3.Connection] = None

    def __enter__(self) -> "ResolutionCache":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_title TEXT NOT NULL UNIQUE,
                normalized_title TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
                season_number INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                expected_episodes INTEGER,
                UNIQUE(show_id, season_number)
            );
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
                episode_number INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                UNIQUE(season_id, episode_number)
            );
            CREATE TABLE IF NOT EXISTS media_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                source_key TEXT NOT NULL UNIQUE,
                episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
                original_path TEXT NOT NULL,
                current_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                release_tag TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'identified',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS resolutions (
                fingerprint TEXT PRIMARY KEY,
                parser_version TEXT NOT NULL,
                show_id INTEGER REFERENCES shows(id) ON DELETE SET NULL,
                episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
                source_name TEXT NOT NULL,
                context_name TEXT NOT NULL,
                canonical_title TEXT NOT NULL,
                season INTEGER,
                episode INTEGER,
                is_movie INTEGER NOT NULL,
                confidence REAL NOT NULL,
                release_tag TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                action TEXT NOT NULL,
                source TEXT NOT NULL,
                destination TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                migration_plan_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                applied_at TEXT
            );
            CREATE VIEW IF NOT EXISTS show_progress AS
            SELECT
                sh.id AS show_id,
                sh.canonical_title,
                se.season_number,
                COUNT(DISTINCT ep.id) AS identified_episodes,
                COUNT(DISTINCT CASE WHEN mf.status='organized' THEN ep.id END) AS organized_episodes,
                COUNT(DISTINCT mf.id) AS media_files
            FROM media_files mf
            JOIN episodes ep ON ep.id=mf.episode_id
            JOIN seasons se ON se.id=ep.season_id
            JOIN shows sh ON sh.id=se.show_id
            GROUP BY sh.id, sh.canonical_title, se.season_number;
            """
        )
        self._migrate_schema()
        self.connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
        self.connection.commit()
        return self

    def _migrate_schema(self) -> None:
        assert self.connection is not None
        version_row = self.connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if version_row is not None and int(version_row[0]) > SCHEMA_VERSION:
            raise RuntimeError(
                "cache schema version %s is newer than supported version %s"
                % (version_row[0], SCHEMA_VERSION)
            )

        with self.connection:
            columns = {
                str(row["name"])
                for row in self.connection.execute("PRAGMA table_info(media_files)")
            }
            if "source_key" not in columns:
                self.connection.execute("ALTER TABLE media_files ADD COLUMN source_key TEXT")

            rows = self.connection.execute(
                "SELECT id, original_path FROM media_files ORDER BY id"
            ).fetchall()
            for row in rows:
                self.connection.execute(
                    "UPDATE media_files SET source_key=? WHERE id=?",
                    (_source_key(row["original_path"]), int(row["id"])),
                )

            duplicate_keys = self.connection.execute(
                """
                SELECT source_key
                FROM media_files
                GROUP BY source_key
                HAVING COUNT(*) > 1
                """
            ).fetchall()
            for duplicate in duplicate_keys:
                duplicate_rows = self.connection.execute(
                    """
                    SELECT id, current_path, status
                    FROM media_files
                    WHERE source_key=?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (duplicate["source_key"],),
                ).fetchall()
                current_id = int(duplicate_rows[0]["id"])
                organized_row = next(
                    (row for row in duplicate_rows if row["status"] == "organized"),
                    None,
                )
                if organized_row is not None:
                    self.connection.execute(
                        "UPDATE media_files SET current_path=?, status='organized' WHERE id=?",
                        (organized_row["current_path"], current_id),
                    )
                superseded_ids = [int(row["id"]) for row in duplicate_rows[1:]]
                self.connection.executemany(
                    "DELETE FROM media_files WHERE id=?",
                    [(row_id,) for row_id in superseded_ids],
                )
                self.connection.execute(
                    "UPDATE media_files SET source_key=? WHERE id=?",
                    (duplicate["source_key"], current_id),
                )

            self.connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_media_files_source_key ON media_files(source_key)"
            )
            self.connection.execute("DROP VIEW IF EXISTS show_progress")
            self.connection.execute(
                """
                CREATE VIEW show_progress AS
                SELECT
                    sh.id AS show_id,
                    sh.canonical_title,
                    se.season_number,
                    COUNT(DISTINCT ep.id) AS identified_episodes,
                    COUNT(DISTINCT CASE WHEN mf.status='organized' THEN ep.id END) AS organized_episodes,
                    COUNT(DISTINCT mf.id) AS media_files
                FROM media_files mf
                JOIN episodes ep ON ep.id=mf.episode_id
                JOIN seasons se ON se.id=ep.season_id
                JOIN shows sh ON sh.id=se.show_id
                GROUP BY sh.id, sh.canonical_title, se.season_number
                """
            )

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.connection is not None:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
            self.connection.close()
            self.connection = None

    def flush(self) -> None:
        assert self.connection is not None
        self.connection.commit()

    def get(self, media: MediaFile, decision_version: str = "") -> Optional[Resolution]:
        assert self.connection is not None
        key = fingerprint(media, decision_version)
        row = self.connection.execute(
            "SELECT * FROM resolutions WHERE fingerprint=? AND parser_version=? AND accepted=1",
            (key, PARSER_VERSION),
        ).fetchone()
        if row is None:
            return None
        evidence_raw = json.loads(row["evidence_json"])
        return Resolution(
            media=media,
            canonical_title=row["canonical_title"],
            season=row["season"],
            episode=row["episode"],
            is_movie=bool(row["is_movie"]),
            confidence=float(row["confidence"]),
            accepted=True,
            release_tag=row["release_tag"],
            evidence=[Evidence(**item) for item in evidence_raw],
            warnings=list(json.loads(row["warnings_json"])),
            fingerprint=key,
        )

    def put(self, resolution: Resolution) -> None:
        assert self.connection is not None
        if not resolution.accepted:
            return
        key = resolution.fingerprint or fingerprint(resolution.media)
        show_id, episode_id = self._upsert_library_entities(resolution)
        self.connection.execute(
            """
            INSERT OR REPLACE INTO resolutions(
                fingerprint, parser_version, show_id, episode_id, source_name, context_name, canonical_title,
                season, episode, is_movie, confidence, release_tag, evidence_json,
                warnings_json, accepted, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP)
            """,
            (
                key, PARSER_VERSION, show_id, episode_id, resolution.media.path.name, resolution.media.context_name,
                resolution.canonical_title, resolution.season, resolution.episode,
                int(resolution.is_movie), resolution.confidence, resolution.release_tag,
                json.dumps([item.__dict__ for item in resolution.evidence], ensure_ascii=False),
                json.dumps(resolution.warnings, ensure_ascii=False),
            ),
        )

    def _upsert_library_entities(self, resolution: Resolution):
        assert self.connection is not None
        normalized = alias_key(resolution.canonical_title)
        self.connection.execute(
            "INSERT OR IGNORE INTO shows(canonical_title, normalized_title) VALUES(?,?)",
            (resolution.canonical_title, normalized),
        )
        show_row = self.connection.execute(
            "SELECT id FROM shows WHERE canonical_title=? OR normalized_title=?",
            (resolution.canonical_title, normalized),
        ).fetchone()
        show_id = int(show_row[0])
        self.connection.execute(
            "INSERT OR IGNORE INTO seasons(show_id, season_number) VALUES(?,?)",
            (show_id, int(1 if resolution.season is None else resolution.season)),
        )
        season_id = int(
            self.connection.execute(
                "SELECT id FROM seasons WHERE show_id=? AND season_number=?",
                (show_id, int(1 if resolution.season is None else resolution.season)),
            ).fetchone()[0]
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO episodes(season_id, episode_number) VALUES(?,?)",
            (season_id, int(resolution.episode or 1)),
        )
        episode_id = int(
            self.connection.execute(
                "SELECT id FROM episodes WHERE season_id=? AND episode_number=?",
                (season_id, int(resolution.episode or 1)),
            ).fetchone()[0]
        )
        key = resolution.fingerprint or fingerprint(resolution.media)
        source_key = _source_key(resolution.media.path)
        self.connection.execute(
            """
            INSERT INTO media_files(
                fingerprint, source_key, episode_id, original_path, current_path, size, mtime_ns,
                release_tag, status, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,'identified',CURRENT_TIMESTAMP)
            ON CONFLICT(source_key) DO UPDATE SET
                fingerprint=excluded.fingerprint,
                episode_id=excluded.episode_id,
                original_path=excluded.original_path,
                current_path=CASE
                    WHEN media_files.size=excluded.size AND media_files.mtime_ns=excluded.mtime_ns
                    THEN media_files.current_path
                    ELSE excluded.original_path
                END,
                status=CASE
                    WHEN media_files.size=excluded.size AND media_files.mtime_ns=excluded.mtime_ns
                    THEN media_files.status
                    ELSE 'identified'
                END,
                size=excluded.size,
                mtime_ns=excluded.mtime_ns,
                release_tag=excluded.release_tag,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                key, source_key, episode_id, str(resolution.media.path), str(resolution.media.path),
                resolution.media.size, resolution.media.mtime_ns, resolution.release_tag,
            ),
        )
        return show_id, episode_id

    def mark_organized(self, resolution: Resolution, destination: Path) -> None:
        assert self.connection is not None
        self.connection.execute(
            "UPDATE media_files SET current_path=?, status='organized', updated_at=CURRENT_TIMESTAMP WHERE source_key=?",
            (str(destination), _source_key(resolution.media.path)),
        )
        self.connection.commit()

    def mark_reverted(self, resolution: Resolution) -> None:
        self.mark_reverted_path(resolution.media.path)

    def mark_reverted_path(self, source: Path) -> None:
        assert self.connection is not None
        self.connection.execute(
            "UPDATE media_files SET current_path=original_path, status='identified', updated_at=CURRENT_TIMESTAMP WHERE source_key=?",
            (_source_key(source),),
        )
        self.connection.commit()

    def mark_reverted_fingerprint(self, key: str) -> None:
        assert self.connection is not None
        self.connection.execute(
            "UPDATE media_files SET current_path=original_path, status='identified', updated_at=CURRENT_TIMESTAMP WHERE fingerprint=?",
            (key,),
        )
        self.connection.commit()

    def list_show_progress(self):
        assert self.connection is not None
        rows = self.connection.execute(
            "SELECT * FROM show_progress ORDER BY canonical_title, season_number"
        ).fetchall()
        return [dict(row) for row in rows]

    def show_detail(self, show_id: int):
        assert self.connection is not None
        show = self.connection.execute("SELECT * FROM shows WHERE id=?", (show_id,)).fetchone()
        if show is None:
            return None
        files = self.connection.execute(
            """
            SELECT se.season_number, ep.episode_number, mf.*,
                   COALESCE(r.is_movie, 0) AS is_movie
            FROM media_files mf
            JOIN episodes ep ON ep.id=mf.episode_id
            JOIN seasons se ON se.id=ep.season_id
            LEFT JOIN resolutions r ON r.fingerprint=mf.fingerprint
            WHERE se.show_id=?
            ORDER BY se.season_number, ep.episode_number, mf.id
            """,
            (show_id,),
        ).fetchall()
        return {"show": dict(show), "episodes": [dict(row) for row in files]}

    def create_correction(self, entity_type: str, entity_id: int, field_name: str, old_value: str, new_value: str, reason: str, migration_plan) -> int:
        assert self.connection is not None
        cursor = self.connection.execute(
            """
            INSERT INTO corrections(entity_type, entity_id, field_name, old_value, new_value, reason, migration_plan_json)
            VALUES(?,?,?,?,?,?,?)
            """,
            (entity_type, entity_id, field_name, old_value, new_value, reason, json.dumps(migration_plan, ensure_ascii=False)),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def record_operation(self, run_id: str, action: str, source: Path, destination: Path, status: str, error: str = "") -> None:
        assert self.connection is not None
        self.connection.execute(
            "INSERT INTO operations(run_id, action, source, destination, status, error) VALUES(?,?,?,?,?,?)",
            (run_id, action, str(source), str(destination), status, error),
        )
        self.connection.commit()

    def reset(self) -> None:
        assert self.connection is not None
        self.connection.executescript(
            """
            DELETE FROM corrections;
            DELETE FROM operations;
            DELETE FROM resolutions;
            DELETE FROM media_files;
            DELETE FROM episodes;
            DELETE FROM seasons;
            DELETE FROM shows;
            """
        )
        self.connection.commit()
