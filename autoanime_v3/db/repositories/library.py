"""Repository for physical file generations and their locations."""

import os
from pathlib import Path

from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.entities import FileLocation, MediaFile
from autoanime_v3.domain.errors import NotFoundError
from autoanime_v3.services.roots import normalize_windows_path


def location_from_row(row):
    return FileLocation(
        id=int(row["id"]),
        media_file_id=int(row["media_file_id"]),
        root_id=int(row["root_id"]),
        path=str(row["path"]),
        normalized_path=str(row["normalized_path"]),
        role=str(row["role"]),
        state=str(row["state"]),
    )


class LibraryRepository:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)

    def observe_path(self, root_id, path, role, media_kind):
        display_path = str(Path(path).resolve(strict=True))
        normalized = normalize_windows_path(path)
        stat = os.stat(display_path)
        size = int(stat.st_size)
        mtime_ns = int(stat.st_mtime_ns)
        volume_serial = str(stat.st_dev)
        file_index = str(stat.st_ino) if int(stat.st_ino) else None

        with SqliteUnitOfWork(self.database_path) as uow:
            existing_location = uow.connection.execute(
                "SELECT * FROM file_locations WHERE normalized_path = ? AND state = 'present'",
                (normalized,),
            ).fetchone()
            if existing_location is not None:
                existing_media = uow.connection.execute(
                    "SELECT * FROM media_files WHERE id = ?",
                    (existing_location["media_file_id"],),
                ).fetchone()
                same_generation = (
                    int(existing_media["size"]) == size
                    and int(existing_media["mtime_ns"]) == mtime_ns
                    and (existing_media["file_index"] or None) == file_index
                )
                if same_generation:
                    media_id = int(existing_media["id"])
                    uow.connection.execute(
                        "UPDATE file_locations SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (existing_location["id"],),
                    )
                    uow.commit()
                    return self.get_media(media_id)
                uow.connection.execute(
                    "UPDATE file_locations SET state = 'replaced', last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (existing_location["id"],),
                )

            media_row = None
            if file_index is not None:
                media_row = uow.connection.execute(
                    """
                    SELECT * FROM media_files
                    WHERE volume_serial = ? AND file_index = ? AND size = ?
                      AND generation_status = 'current'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (volume_serial, file_index, size),
                ).fetchone()
            if media_row is None:
                cursor = uow.connection.execute(
                    """
                    INSERT INTO media_files(
                        size, mtime_ns, volume_serial, file_index, media_kind, generation_status
                    ) VALUES (?, ?, ?, ?, ?, 'current')
                    """,
                    (size, mtime_ns, volume_serial, file_index, media_kind),
                )
                media_id = int(cursor.lastrowid)
            else:
                media_id = int(media_row["id"])

            uow.connection.execute(
                """
                INSERT INTO file_locations(
                    media_file_id, root_id, path, normalized_path, role, state
                ) VALUES (?, ?, ?, ?, ?, 'present')
                """,
                (media_id, root_id, display_path, normalized, role),
            )
            uow.commit()
        return self.get_media(media_id)

    def get_media(self, media_file_id):
        from autoanime_v3.db.engine import connect_sqlite

        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            row = connection.execute(
                "SELECT * FROM media_files WHERE id = ?", (media_file_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Media file does not exist", {"id": media_file_id})
            locations = connection.execute(
                "SELECT * FROM file_locations WHERE media_file_id = ? ORDER BY id",
                (media_file_id,),
            ).fetchall()
            return MediaFile(
                id=int(row["id"]),
                size=int(row["size"]),
                mtime_ns=int(row["mtime_ns"]),
                volume_serial=row["volume_serial"],
                file_index=row["file_index"],
                sha256=row["sha256"],
                media_kind=str(row["media_kind"]),
                generation_status=str(row["generation_status"]),
                locations=tuple(location_from_row(item) for item in locations),
            )
        finally:
            connection.close()

