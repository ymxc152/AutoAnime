"""Lease-based SQLite job queue."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.repositories.jobs import JobRepository, job_from_row
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import InvalidStateError, LeaseConflictError, NotFoundError


def utc_now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


class JobQueue:
    def __init__(self, database_path, clock=None):
        self.database_path = Path(database_path)
        self.clock = clock or utc_now
        run_migrations(self.database_path)

    def enqueue(self, job_type, payload, idempotency_key=None, priority=0):
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = JobRepository(uow.connection)
            existing = repository.find_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing
            job = repository.enqueue(
                job_type, payload, idempotency_key, int(priority), iso(self.clock())
            )
            uow.commit()
            return job

    def _interrupt_expired(self, connection, now):
        connection.execute(
            """
            UPDATE jobs
            SET status = 'interrupted', error_code = 'lease_expired',
                error_summary = 'Worker lease expired before completion',
                lease_owner = NULL, lease_until = NULL, heartbeat_at = NULL,
                finished_at = ?
            WHERE status IN ('leased', 'running')
              AND lease_until IS NOT NULL AND lease_until <= ?
            """,
            (iso(now), iso(now)),
        )

    def lease_next(self, worker_id, lease_seconds):
        now = self.clock()
        with SqliteUnitOfWork(self.database_path) as uow:
            self._interrupt_expired(uow.connection, now)
            repository = JobRepository(uow.connection)
            candidate = repository.next_queued()
            if candidate is None:
                uow.commit()
                return None
            lease_until = now + timedelta(seconds=lease_seconds)
            updated = uow.connection.execute(
                """
                UPDATE jobs
                SET status = 'leased', lease_owner = ?, lease_until = ?, heartbeat_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (worker_id, iso(lease_until), iso(now), candidate.id),
            ).rowcount
            if updated != 1:
                uow.commit()
                return None
            leased = repository.get(candidate.id)
            uow.commit()
            return leased

    def start(self, job_id, worker_id):
        now = iso(self.clock())
        with SqliteUnitOfWork(self.database_path) as uow:
            updated = uow.connection.execute(
                """
                UPDATE jobs SET status = 'running', started_at = COALESCE(started_at, ?)
                WHERE id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (now, job_id, worker_id),
            ).rowcount
            if updated != 1:
                raise LeaseConflictError("Worker does not own the leased job")
            job = JobRepository(uow.connection).get(job_id)
            uow.commit()
            return job

    def heartbeat(self, job_id, worker_id, lease_seconds=60):
        now = self.clock()
        with SqliteUnitOfWork(self.database_path) as uow:
            updated = uow.connection.execute(
                """
                UPDATE jobs SET heartbeat_at = ?, lease_until = ?
                WHERE id = ? AND lease_owner = ? AND status IN ('leased', 'running')
                  AND lease_until > ?
                """,
                (
                    iso(now),
                    iso(now + timedelta(seconds=lease_seconds)),
                    job_id,
                    worker_id,
                    iso(now),
                ),
            ).rowcount
            if updated != 1:
                raise LeaseConflictError("Worker cannot renew this job lease")
            job = JobRepository(uow.connection).get(job_id)
            uow.commit()
            return job

    def append_event(self, job_id, event_type, payload, message="", level="info"):
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = JobRepository(uow.connection)
            if repository.get(job_id) is None:
                raise NotFoundError("Job does not exist", {"id": job_id})
            event = repository.append_event(
                job_id, event_type, payload, message, level, iso(self.clock())
            )
            uow.commit()
            return event

    def events(self, job_id, after_sequence=0):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            return JobRepository(connection).events(job_id, after_sequence)
        finally:
            connection.close()

    def request_cancel(self, job_id):
        now = iso(self.clock())
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = JobRepository(uow.connection)
            job = repository.get(job_id)
            if job is None:
                raise NotFoundError("Job does not exist", {"id": job_id})
            if job.status == "queued":
                uow.connection.execute(
                    "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                    (now, job_id),
                )
            elif job.status in {"leased", "running"}:
                uow.connection.execute(
                    "UPDATE jobs SET cancel_requested_at = ? WHERE id = ?", (now, job_id)
                )
            else:
                raise InvalidStateError("Job cannot be cancelled in its current state")
            result = repository.get(job_id)
            uow.commit()
            return result

    def cancel_at_safe_boundary(self, job_id, worker_id):
        now = iso(self.clock())
        with SqliteUnitOfWork(self.database_path) as uow:
            updated = uow.connection.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', lease_owner = NULL, lease_until = NULL,
                    heartbeat_at = NULL, finished_at = ?
                WHERE id = ? AND lease_owner = ? AND status IN ('leased', 'running')
                  AND cancel_requested_at IS NOT NULL
                """,
                (now, job_id, worker_id),
            ).rowcount
            if updated != 1:
                raise InvalidStateError("Cancellation is not pending at a safe boundary")
            job = JobRepository(uow.connection).get(job_id)
            uow.commit()
            return job

    def complete(self, job_id, worker_id):
        now = iso(self.clock())
        with SqliteUnitOfWork(self.database_path) as uow:
            updated = uow.connection.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', lease_owner = NULL, lease_until = NULL,
                    heartbeat_at = NULL, finished_at = ?
                WHERE id = ? AND lease_owner = ? AND status = 'running'
                """,
                (now, job_id, worker_id),
            ).rowcount
            if updated != 1:
                raise LeaseConflictError("Worker cannot complete this job")
            job = JobRepository(uow.connection).get(job_id)
            uow.commit()
            return job

    def fail(self, job_id, worker_id, error_code, summary):
        now = iso(self.clock())
        with SqliteUnitOfWork(self.database_path) as uow:
            updated = uow.connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', error_code = ?, error_summary = ?,
                    lease_owner = NULL, lease_until = NULL, heartbeat_at = NULL,
                    finished_at = ?
                WHERE id = ? AND lease_owner = ? AND status IN ('leased', 'running')
                """,
                (error_code, summary, now, job_id, worker_id),
            ).rowcount
            if updated != 1:
                raise LeaseConflictError("Worker cannot fail this job")
            job = JobRepository(uow.connection).get(job_id)
            uow.commit()
            return job

    def get(self, job_id):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            job = JobRepository(connection).get(job_id)
            if job is None:
                raise NotFoundError("Job does not exist", {"id": job_id})
            return job
        finally:
            connection.close()

