import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class PersistentJobQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "jobs.sqlite3"
        self.clock = MutableClock()

        from autoanime_v3.jobs.queue import JobQueue

        self.queue = JobQueue(self.database, clock=self.clock)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_enqueue_is_idempotent(self):
        first = self.queue.enqueue("scan", {"profile_id": 1}, "scan-profile-1")
        second = self.queue.enqueue("scan", {"profile_id": 1}, "scan-profile-1")
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.status, "queued")

    def test_only_one_worker_can_lease_and_heartbeat_requires_owner(self):
        from autoanime_v3.domain.errors import LeaseConflictError

        job = self.queue.enqueue("scan", {}, "one-lease")
        leased = self.queue.lease_next("worker-a", 60)
        self.assertEqual(leased.id, job.id)
        self.assertEqual(leased.lease_owner, "worker-a")
        self.assertIsNone(self.queue.lease_next("worker-b", 60))
        renewed = self.queue.heartbeat(job.id, "worker-a", 120)
        self.assertEqual(renewed.lease_owner, "worker-a")
        with self.assertRaises(LeaseConflictError):
            self.queue.heartbeat(job.id, "worker-b", 120)

    def test_expired_file_changing_lease_becomes_interrupted_not_requeued(self):
        job = self.queue.enqueue("execute_plan", {"plan_id": 8}, "execute-8")
        self.queue.lease_next("worker-a", 30)
        self.queue.start(job.id, "worker-a")
        self.clock.advance(seconds=31)

        self.assertIsNone(self.queue.lease_next("worker-b", 30))
        recovered = self.queue.get(job.id)
        self.assertEqual(recovered.status, "interrupted")
        self.assertEqual(recovered.error_code, "lease_expired")

    def test_events_have_strictly_increasing_sequences(self):
        job = self.queue.enqueue("scan", {}, "events")
        first = self.queue.append_event(job.id, "phase", {"name": "discover"}, "开始扫描")
        second = self.queue.append_event(job.id, "progress", {"current": 1}, "发现文件")
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual([event.sequence for event in self.queue.events(job.id)], [1, 2])

    def test_running_cancellation_waits_for_safe_boundary(self):
        job = self.queue.enqueue("execute_plan", {}, "cancel")
        self.queue.lease_next("worker-a", 60)
        self.queue.start(job.id, "worker-a")
        requested = self.queue.request_cancel(job.id)
        self.assertEqual(requested.status, "running")
        self.assertTrue(requested.cancel_requested)
        cancelled = self.queue.cancel_at_safe_boundary(job.id, "worker-a")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNone(cancelled.lease_owner)

    def test_worker_crash_does_not_silently_repeat_unknown_file_operation(self):
        from autoanime_v3.jobs.worker import Worker

        job = self.queue.enqueue("execute_plan", {"plan_id": 99}, "crash-recovery")
        worker = Worker("worker-a", self.queue, {"execute_plan": lambda unused: None})
        leased = worker.acquire(lease_seconds=10)
        self.assertEqual(leased.id, job.id)
        self.queue.start(job.id, "worker-a")
        self.clock.advance(seconds=11)

        replacement = Worker("worker-b", self.queue, {"execute_plan": lambda unused: None})
        self.assertIsNone(replacement.acquire(lease_seconds=10))
        self.assertEqual(self.queue.get(job.id).status, "interrupted")

    def test_worker_renews_lease_for_entire_handler_lifetime(self):
        from autoanime_v3.jobs.queue import JobQueue
        from autoanime_v3.jobs.worker import Worker

        queue = JobQueue(self.database)
        job = queue.enqueue("scan", {}, "long-handler")
        started = threading.Event()
        release = threading.Event()

        def long_handler(unused_job):
            started.set()
            self.assertTrue(release.wait(3))

        worker = Worker("worker-a", queue, {"scan": long_handler})
        thread = threading.Thread(target=worker.run_once, kwargs={"lease_seconds": 0.3})
        thread.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.5)

        replacement = Worker("worker-b", queue, {"scan": lambda unused: None})
        self.assertIsNone(replacement.acquire(lease_seconds=0.3))
        self.assertEqual(queue.get(job.id).lease_owner, "worker-a")

        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(queue.get(job.id).status, "succeeded")


if __name__ == "__main__":
    unittest.main()
