"""Versioned JSON rule documents."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.entities import RuleRevisionView, RuleSetView
from autoanime_v3.domain.errors import InvalidStateError, NotFoundError, ValidationError


def canonical_document(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def revision_view(row):
    return RuleRevisionView(
        int(row["id"]),
        int(row["rule_set_id"]),
        int(row["revision"]),
        json.loads(row["document_json"]),
        row["content_hash"],
        str(row["status"]),
    )


RULE_DOCUMENT_SECTIONS = (
    "aliases",
    "season_layouts",
    "episode_defaults",
    "season_defaults",
)


@dataclass(frozen=True)
class ActiveRuleDocument:
    revision_ids: tuple
    document: dict
    content_hash: str


def active_rule_document(connection):
    rows = connection.execute(
        """
        SELECT rr.id, rr.document_json
        FROM rule_sets rs
        JOIN rule_revisions rr ON rr.id = rs.active_revision_id
        ORDER BY rs.id ASC
        """
    ).fetchall()
    merged = {section: {} for section in RULE_DOCUMENT_SECTIONS}
    revision_ids = []
    for row in rows:
        revision_ids.append(int(row["id"]))
        document = json.loads(row["document_json"])
        for section in RULE_DOCUMENT_SECTIONS:
            values = document.get(section, {}) if isinstance(document, dict) else {}
            if isinstance(values, dict):
                merged[section].update(values)
    content_hash = hashlib.sha256(canonical_document(merged).encode("utf-8")).hexdigest()
    return ActiveRuleDocument(tuple(revision_ids), merged, content_hash)


class RuleService:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)

    def create_set(self, name):
        with SqliteUnitOfWork(self.database_path) as uow:
            cursor = uow.connection.execute("INSERT INTO rule_sets(name) VALUES (?)", (name,))
            rule_set = RuleSetView(int(cursor.lastrowid), name, None)
            uow.commit()
            return rule_set

    def get_set(self, rule_set_id):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            row = connection.execute("SELECT * FROM rule_sets WHERE id = ?", (rule_set_id,)).fetchone()
            if row is None:
                raise NotFoundError("Rule set does not exist")
            return RuleSetView(int(row["id"]), str(row["name"]), row["active_revision_id"])
        finally:
            connection.close()

    def get_active(self, connection=None):
        owns_connection = connection is None
        if owns_connection:
            connection = connect_sqlite(self.database_path)
            connection.row_factory = __import__("sqlite3").Row
        try:
            return active_rule_document(connection)
        finally:
            if owns_connection:
                connection.close()

    def _mark_changed_plans_stale(self, connection):
        current_version = active_rule_document(connection).content_hash
        connection.execute(
            """
            UPDATE plans SET status = 'stale'
            WHERE status IN ('draft', 'ready', 'approved', 'executing') AND rule_version != ?
            """,
            (current_version,),
        )

    def create_revision(self, rule_set_id, document):
        with SqliteUnitOfWork(self.database_path) as uow:
            if uow.connection.execute("SELECT 1 FROM rule_sets WHERE id = ?", (rule_set_id,)).fetchone() is None:
                raise NotFoundError("Rule set does not exist")
            number = int(
                uow.connection.execute(
                    "SELECT COALESCE(MAX(revision), 0) + 1 FROM rule_revisions WHERE rule_set_id = ?",
                    (rule_set_id,),
                ).fetchone()[0]
            )
            cursor = uow.connection.execute(
                """
                INSERT INTO rule_revisions(rule_set_id, revision, document_json, status)
                VALUES (?, ?, ?, 'draft')
                """,
                (rule_set_id, number, canonical_document(document)),
            )
            row = uow.connection.execute(
                "SELECT * FROM rule_revisions WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            uow.commit()
            return revision_view(row)

    def _get_revision_row(self, connection, revision_id):
        row = connection.execute("SELECT * FROM rule_revisions WHERE id = ?", (revision_id,)).fetchone()
        if row is None:
            raise NotFoundError("Rule revision does not exist")
        return row

    def validate(self, revision_id):
        with SqliteUnitOfWork(self.database_path) as uow:
            row = self._get_revision_row(uow.connection, revision_id)
            document = json.loads(row["document_json"])
            errors = []
            if not isinstance(document, dict):
                errors.append("document must be an object")
            if "aliases" in document and not isinstance(document["aliases"], dict):
                errors.append("aliases must be an object")
            if errors:
                uow.connection.execute(
                    "UPDATE rule_revisions SET validation_errors_json = ? WHERE id = ?",
                    (json.dumps(errors), revision_id),
                )
                uow.commit()
                raise ValidationError("Rule document is invalid", {"errors": errors})
            digest = hashlib.sha256(canonical_document(document).encode("utf-8")).hexdigest()
            uow.connection.execute(
                """
                UPDATE rule_revisions
                SET status = 'validated', content_hash = ?, validation_errors_json = NULL
                WHERE id = ?
                """,
                (digest, revision_id),
            )
            result = revision_view(self._get_revision_row(uow.connection, revision_id))
            uow.commit()
            return result

    def activate(self, revision_id):
        with SqliteUnitOfWork(self.database_path) as uow:
            row = self._get_revision_row(uow.connection, revision_id)
            if row["status"] not in {"validated", "active"}:
                raise InvalidStateError("Rule revision must be validated before activation")
            uow.connection.execute(
                "UPDATE rule_revisions SET status = 'retired' WHERE rule_set_id = ? AND status = 'active'",
                (row["rule_set_id"],),
            )
            uow.connection.execute("UPDATE rule_revisions SET status = 'active' WHERE id = ?", (revision_id,))
            uow.connection.execute(
                "UPDATE rule_sets SET active_revision_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (revision_id, row["rule_set_id"]),
            )
            self._mark_changed_plans_stale(uow.connection)
            result = revision_view(self._get_revision_row(uow.connection, revision_id))
            uow.commit()
            return result

    def rollback(self, rule_set_id, revision_id):
        with SqliteUnitOfWork(self.database_path) as uow:
            row = self._get_revision_row(uow.connection, revision_id)
            if int(row["rule_set_id"]) != int(rule_set_id) or not row["content_hash"]:
                raise InvalidStateError("Revision cannot be activated for this rule set")
            uow.connection.execute(
                "UPDATE rule_revisions SET status = 'retired' WHERE rule_set_id = ? AND status = 'active'",
                (rule_set_id,),
            )
            uow.connection.execute("UPDATE rule_revisions SET status = 'active' WHERE id = ?", (revision_id,))
            uow.connection.execute(
                "UPDATE rule_sets SET active_revision_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (revision_id, rule_set_id),
            )
            self._mark_changed_plans_stale(uow.connection)
            result = revision_view(self._get_revision_row(uow.connection, revision_id))
            uow.commit()
            return result
