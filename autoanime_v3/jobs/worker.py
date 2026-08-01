"""Worker orchestration that never auto-retries interrupted file changes."""

import threading


class Worker:
    def __init__(self, worker_id, queue, handlers):
        self.worker_id = worker_id
        self.queue = queue
        self.handlers = dict(handlers)

    def acquire(self, lease_seconds=60):
        return self.queue.lease_next(self.worker_id, lease_seconds)

    def run_once(self, lease_seconds=60):
        job = self.acquire(lease_seconds)
        if job is None:
            return None
        handler = self.handlers.get(job.job_type)
        if handler is None:
            self.queue.fail(job.id, self.worker_id, "unknown_job_type", job.job_type)
            return self.queue.get(job.id)
        self.queue.start(job.id, self.worker_id)
        heartbeat_stop = threading.Event()
        heartbeat_error = []

        def renew_lease():
            interval = max(float(lease_seconds) / 3.0, 0.05)
            while not heartbeat_stop.wait(interval):
                try:
                    self.queue.heartbeat(job.id, self.worker_id, lease_seconds)
                except Exception as error:
                    heartbeat_error.append(error)
                    return

        heartbeat_thread = threading.Thread(
            target=renew_lease,
            name="autoanime-heartbeat-%s" % job.id,
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            handler(job)
        except Exception as error:
            heartbeat_stop.set()
            heartbeat_thread.join()
            self.queue.fail(job.id, self.worker_id, "handler_failed", str(error))
        else:
            heartbeat_stop.set()
            heartbeat_thread.join()
            if heartbeat_error:
                return self.queue.get(job.id)
            current = self.queue.get(job.id)
            if current.cancel_requested:
                self.queue.cancel_at_safe_boundary(job.id, self.worker_id)
            else:
                self.queue.complete(job.id, self.worker_id)
        return self.queue.get(job.id)
