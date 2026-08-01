from autoanime_v3.domain.entities import StorageRoot


def root_from_row(row):
    return StorageRoot(
        id=int(row["id"]),
        kind=str(row["kind"]),
        path=str(row["path"]),
        normalized_path=str(row["normalized_path"]),
        enabled=bool(row["enabled"]),
        health_status=str(row["health_status"]),
        volume_serial=row["volume_serial"],
        filesystem_type=row["filesystem_type"],
    )


class RootRepository:
    def __init__(self, connection):
        self.connection = connection

    def create(self, kind, path, normalized_path):
        cursor = self.connection.execute(
            """
            INSERT INTO storage_roots(kind, path, normalized_path)
            VALUES (?, ?, ?)
            """,
            (kind, path, normalized_path),
        )
        return self.get(cursor.lastrowid)

    def get(self, root_id):
        row = self.connection.execute(
            "SELECT * FROM storage_roots WHERE id = ?", (root_id,)
        ).fetchone()
        return root_from_row(row) if row is not None else None

    def find_by_normalized_path(self, normalized_path):
        row = self.connection.execute(
            "SELECT * FROM storage_roots WHERE normalized_path = ?",
            (normalized_path,),
        ).fetchone()
        return root_from_row(row) if row is not None else None

    def list_enabled(self):
        rows = self.connection.execute(
            "SELECT * FROM storage_roots WHERE enabled = 1 ORDER BY id"
        ).fetchall()
        return tuple(root_from_row(row) for row in rows)

    def update_health(self, root_id, status, checked_at, volume_serial=None):
        self.connection.execute(
            """
            UPDATE storage_roots
            SET health_status = ?, last_checked_at = ?, volume_serial = COALESCE(?, volume_serial),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, checked_at, volume_serial, root_id),
        )
        return self.get(root_id)

