"""Web-facing job command/query facade."""


class JobService:
    def __init__(self, queue):
        self.queue = queue

    def submit_scan(self, profile_id, paths=None, idempotency_key=None):
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
