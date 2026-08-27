from autoanime_v3.domain.entities import ScanProfile


def profile_from_row(row):
    return ScanProfile(
        id=int(row["id"]),
        name=str(row["name"]),
        source_root_id=int(row["source_root_id"]),
        library_root_id=int(row["library_root_id"]),
        mode=str(row["mode"]),
        execution_policy=str(row["execution_policy"]),
        min_confidence=int(row["min_confidence"]),
        stability_seconds=int(row["stability_seconds"]),
        watch_enabled=bool(row["watch_enabled"]),
        enabled=bool(row["enabled"]),
        revision=int(row["revision"]),
    )


class ProfileRepository:
    def __init__(self, connection):
        self.connection = connection

    def create(self, command):
        cursor = self.connection.execute(
            """
            INSERT INTO scan_profiles(
                name, source_root_id, library_root_id, mode, execution_policy,
                min_confidence, stability_seconds, watch_enabled, enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command.name,
                command.source_root_id,
                command.library_root_id,
                command.mode,
                command.execution_policy,
                command.min_confidence,
                command.stability_seconds,
                int(command.watch_enabled),
                int(command.enabled),
            ),
        )
        return self.get(cursor.lastrowid)

    def get(self, profile_id):
        row = self.connection.execute(
            "SELECT * FROM scan_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return profile_from_row(row) if row is not None else None

    def update(self, profile_id, revision, patch):
        allowed = {
            "name",
            "source_root_id",
            "library_root_id",
            "mode",
            "execution_policy",
            "min_confidence",
            "stability_seconds",
            "watch_enabled",
            "enabled",
        }
        fields = []
        values = []
        for key, value in patch.items():
            if key not in allowed:
                continue
            fields.append("%s = ?" % key)
            values.append(int(value) if key in {"watch_enabled", "enabled"} else value)
        if not fields:
            return self.get(profile_id), False
        fields.extend(["revision = revision + 1", "updated_at = CURRENT_TIMESTAMP"])
        values.extend([profile_id, revision])
        cursor = self.connection.execute(
            "UPDATE scan_profiles SET %s WHERE id = ? AND revision = ?" % ", ".join(fields),
            tuple(values),
        )
        return self.get(profile_id), cursor.rowcount == 1

