"""Scan persistence helpers."""

import json


class ScanRepository:
    def __init__(self, connection):
        self.connection = connection

    def create_run(self, profile_id, profile_revision, rule_version, scope, started_at):
        cursor = self.connection.execute(
            """
            INSERT INTO scan_runs(
                profile_id, profile_revision, rule_version, scope_json,
                statistics_json, started_at
            ) VALUES (?, ?, ?, ?, '{}', ?)
            """,
            (profile_id, profile_revision, rule_version, json.dumps(scope), started_at),
        )
        return int(cursor.lastrowid)

    def add_item(self, run_id, media_file_id, path, normalized_path, snapshot, outcome, reason=None):
        self.connection.execute(
            """
            INSERT INTO scan_items(
                scan_run_id, media_file_id, path, normalized_path,
                snapshot_json, outcome, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                media_file_id,
                path,
                normalized_path,
                json.dumps(snapshot, ensure_ascii=False),
                outcome,
                reason,
            ),
        )

    def finish(self, run_id, statistics, finished_at):
        self.connection.execute(
            "UPDATE scan_runs SET statistics_json = ?, finished_at = ? WHERE id = ?",
            (json.dumps(statistics, ensure_ascii=False), finished_at, run_id),
        )

