"""Web-facing job command/query facade."""

import json

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.domain.errors import NotFoundError, ValidationError


class JobService:
    def __init__(self, queue):
        self.queue = queue

    def submit_scan(self, profile_id, paths=None, idempotency_key=None):
        connection = connect_sqlite(self.queue.database_path)
        try:
            profile = connection.execute(
                "SELECT deleted_at FROM scan_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        finally:
            connection.close()
        if profile is None:
            raise NotFoundError("Scan profile does not exist", {"id": profile_id})
        if profile[0] is not None:
            raise ValidationError("Scan profile has been deleted and cannot start new scans", {"profile_id": profile_id})
        return self.queue.enqueue(
            "scan",
            {"profile_id": profile_id, "paths": list(paths or [])},
            idempotency_key=idempotency_key,
        )

    def cancel(self, job_id):
        return self.queue.request_cancel(job_id)

    def get(self, job_id):
        return self.queue.get(job_id)

    def events(self, job_id, after_sequence=0):
        return self.queue.events(job_id, after_sequence)
