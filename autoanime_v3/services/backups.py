"""SQLite online backup, checksum, and maintenance-mode restore."""

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import SCHEMA_VERSION, run_migrations
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.entities import BackupRecordView
from autoanime_v3.domain.errors import NotFoundError, ValidationError


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BackupService:
    def __init__(self, database_path, backup_directory):
        self.database_path = Path(database_path)
        self.backup_directory = Path(backup_directory)
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        run_migrations(self.database_path)

    def create(self, kind="manual", sanitized=False):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        destination = self.backup_directory / ("autoanime_%s.sqlite3" % stamp)
        source_connection = connect_sqlite(self.database_path)
        destination_connection = sqlite3.connect(str(destination))
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
            source_connection.close()
        checksum = file_sha256(destination)
        size = destination.stat().st_size
        with SqliteUnitOfWork(self.database_path) as uow:
            cursor = uow.connection.execute(
                """
                INSERT INTO backup_records(
                    path, kind, size, sha256, schema_version, sanitized
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(destination), kind, size, checksum, SCHEMA_VERSION, int(sanitized)),
            )
            backup_id = int(cursor.lastrowid)
            created_at = str(
                uow.connection.execute(
                    "SELECT created_at FROM backup_records WHERE id = ?", (backup_id,)
                ).fetchone()[0]
            )
            uow.commit()
        return BackupRecordView(
            backup_id, str(destination), kind, size, checksum, SCHEMA_VERSION, sanitized, created_at
        )

    def restore(self, backup_id, maintenance_mode=False):
        if not maintenance_mode:
            raise ValidationError("Restore requires maintenance mode")
        connection = connect_sqlite(self.database_path)
        try:
            row = connection.execute(
                "SELECT path, sha256, schema_version FROM backup_records WHERE id = ?", (backup_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise NotFoundError("Backup record does not exist")
        source_path = Path(row[0])
        if not source_path.is_file() or file_sha256(source_path) != row[1]:
            raise ValidationError("Backup file is missing or its checksum changed")
        source_connection = sqlite3.connect(str(source_path))
        target_connection = connect_sqlite(self.database_path)
        try:
            integrity = source_connection.execute("PRAGMA integrity_check").fetchone()[0]
            versions = source_connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if integrity != "ok" or versions is None or int(versions[0]) != SCHEMA_VERSION:
                raise ValidationError("Backup schema or integrity validation failed")
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        run_migrations(self.database_path)
