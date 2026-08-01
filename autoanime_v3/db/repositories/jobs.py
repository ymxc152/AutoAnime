"""Persistent job and event repository."""

import json

from autoanime_v3.domain.entities import Job, JobEvent


def job_from_row(row):
    return Job(
        id=int(row["id"]),
        job_type=str(row["job_type"]),
        status=str(row["status"]),
        priority=int(row["priority"]),
        payload=json.loads(row["payload_json"] or "{}"),
        idempotency_key=row["idempotency_key"],
        progress_current=int(row["progress_current"]),
        progress_total=int(row["progress_total"]),
        current_stage=row["current_stage"],
        error_code=row["error_code"],
        error_summary=row["error_summary"],
        lease_owner=row["lease_owner"],
        lease_until=row["lease_until"],
        cancel_requested=row["cancel_requested_at"] is not None,
        created_at=str(row["created_at"]),
    )


def event_from_row(row):
    return JobEvent(
        id=int(row["id"]),
        job_id=int(row["job_id"]),
        sequence=int(row["sequence"]),
        level=str(row["level"]),
        event_type=str(row["event_type"]),
        message=str(row["message"]),
        payload=json.loads(row["payload_json"] or "{}"),
        created_at=str(row["created_at"]),
    )


class JobRepository:
    def __init__(self, connection):
        self.connection = connection

    def get(self, job_id):
        row = self.connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return job_from_row(row) if row is not None else None

    def find_by_idempotency_key(self, key):
        if key is None:
            return None
        row = self.connection.execute(
            "SELECT * FROM jobs WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return job_from_row(row) if row is not None else None

    def enqueue(self, job_type, payload, idempotency_key, priority, now):
        cursor = self.connection.execute(
            """
            INSERT INTO jobs(job_type, status, priority, payload_json, idempotency_key, created_at)
            VALUES (?, 'queued', ?, ?, ?, ?)
            """,
            (job_type, priority, json.dumps(payload, ensure_ascii=False), idempotency_key, now),
        )
        return self.get(cursor.lastrowid)

    def next_queued(self):
        row = self.connection.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY priority DESC, id ASC LIMIT 1"
        ).fetchone()
        return job_from_row(row) if row is not None else None

    def events(self, job_id, after_sequence=0):
        rows = self.connection.execute(
            """
            SELECT * FROM job_events
            WHERE job_id = ? AND sequence > ? ORDER BY sequence
            """,
            (job_id, after_sequence),
        ).fetchall()
        return tuple(event_from_row(row) for row in rows)

    def append_event(self, job_id, event_type, payload, message, level, now):
        sequence = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM job_events WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        )
        cursor = self.connection.execute(
            """
            INSERT INTO job_events(job_id, sequence, level, event_type, message, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, sequence, level, event_type, message, json.dumps(payload, ensure_ascii=False), now),
        )
        row = self.connection.execute(
            "SELECT * FROM job_events WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return event_from_row(row)

