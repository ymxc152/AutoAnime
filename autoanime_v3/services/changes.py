"""Auditable library corrections with optimistic concurrency."""

import json
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.entities import ChangeRequestView, ShowView
from autoanime_v3.domain.errors import NotFoundError, RevisionConflictError
from autoanime_v3.normalize import alias_key


def show_view(row):
    return ShowView(
        int(row["id"]),
        str(row["canonical_title"]),
        str(row["normalized_key"]),
        str(row["status"]),
        bool(row["title_locked"]),
        int(row["revision"]),
    )


class ChangeService:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)

    def create_show(self, title):
        with SqliteUnitOfWork(self.database_path) as uow:
            cursor = uow.connection.execute(
                "INSERT INTO shows(canonical_title, normalized_key) VALUES (?, ?)",
                (title, alias_key(title)),
            )
            row = uow.connection.execute("SELECT * FROM shows WHERE id = ?", (cursor.lastrowid,)).fetchone()
            uow.commit()
            return show_view(row)

    def _get_show(self, connection, show_id):
        row = connection.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
        if row is None:
            raise NotFoundError("Show does not exist")
        return row

    def preview_show_change(self, show_id, base_revision, patch, reason):
        with SqliteUnitOfWork(self.database_path) as uow:
            show = self._get_show(uow.connection, show_id)
            if int(show["revision"]) != int(base_revision):
                raise RevisionConflictError(
                    "Show changed after the editor loaded it",
                    {"actual_revision": int(show["revision"])},
                )
            old_values = {key: show[key] for key in patch}
            new_values = dict(patch)
            cursor = uow.connection.execute(
                """
                INSERT INTO change_requests(
                    target_type, target_id, patch_json, old_values_json,
                    new_values_json, reason, base_revision, status
                ) VALUES ('show', ?, ?, ?, ?, ?, ?, 'validated')
                """,
                (
                    show_id,
                    json.dumps(patch, ensure_ascii=False),
                    json.dumps(old_values, ensure_ascii=False),
                    json.dumps(new_values, ensure_ascii=False),
                    reason,
                    base_revision,
                ),
            )
            request = ChangeRequestView(
                int(cursor.lastrowid), "show", show_id, old_values, new_values, reason, base_revision, "validated"
            )
            uow.commit()
            return request

    def apply(self, request_id):
        with SqliteUnitOfWork(self.database_path) as uow:
            request = uow.connection.execute(
                "SELECT * FROM change_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise NotFoundError("Change request does not exist")
            show = self._get_show(uow.connection, request["target_id"])
            if int(show["revision"]) != int(request["base_revision"]):
                raise RevisionConflictError("Show changed before applying the request")
            patch = json.loads(request["patch_json"])
            title = patch.get("canonical_title", show["canonical_title"])
            locked = int(bool(patch.get("title_locked", show["title_locked"])))
            uow.connection.execute(
                """
                UPDATE shows SET canonical_title = ?, normalized_key = ?, title_locked = ?,
                    revision = revision + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (title, alias_key(title), locked, show["id"]),
            )
            uow.connection.execute(
                "UPDATE change_requests SET status = 'applied', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (request_id,),
            )
            result = show_view(self._get_show(uow.connection, show["id"]))
            uow.commit()
            return result

