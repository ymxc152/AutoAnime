import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "web.sqlite3"
        self.source = self.root / "source"
        self.library = self.root / "library"
        self.source.mkdir()
        self.library.mkdir()

        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService

        roots = RootService(self.database)
        source_root = roots.create_root("source", self.source)
        library_root = roots.create_root("library", self.library)
        self.profile = ProfileService(self.database).create_profile(
            CreateProfile(
                "automation",
                source_root.id,
                library_root.id,
                stability_seconds=1,
                watch_enabled=True,
            )
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def jobs(self):
        connection = sqlite3.connect(str(self.database))
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY id")]
        finally:
            connection.close()

    def test_watcher_debounces_and_requires_stable_file(self):
        from autoanime_v3.jobs.watcher import StableFileBuffer

        clock = MutableClock()
        buffer = StableFileBuffer(clock=clock, debounce_seconds=2, stability_seconds=5)
        path = Path("C:/Downloads/show.mkv")
        buffer.record(path, 100, 1)
        buffer.record(path, 120, 2)
        self.assertEqual(buffer.ready(), ())
        clock.advance(seconds=4)
        self.assertEqual(buffer.ready(), ())
        clock.advance(seconds=2)
        self.assertEqual(buffer.ready(), (path,))
        buffer.record(Path("C:/Downloads/show.!qB"), 1, 1)
        clock.advance(seconds=10)
        self.assertEqual(buffer.ready(), ())

    def test_interval_schedule_tick_is_atomic_and_restart_safe(self):
        from autoanime_v3.services.automation import AutomationRuntime, ScheduleService

        clock = MutableClock()
        schedule = ScheduleService(self.database, clock=clock).create(
            self.profile.id,
            "interval",
            {"interval_minutes": 5},
            "UTC",
        )
        self.assertEqual(schedule.next_run_at, "2026-07-25T10:05:00+00:00")

        clock.advance(minutes=5)
        first_runtime = AutomationRuntime(self.database, clock=clock, watch_enabled=False)
        first_runtime.tick()
        second_runtime = AutomationRuntime(self.database, clock=clock, watch_enabled=False)
        second_runtime.tick()

        jobs = self.jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["idempotency_key"], "schedule:%s:2026-07-25T10:05:00+00:00" % schedule.id)
        refreshed = ScheduleService(self.database, clock=clock).get(schedule.id)
        self.assertEqual(refreshed.last_run_at, "2026-07-25T10:05:00+00:00")
        self.assertEqual(refreshed.next_run_at, "2026-07-25T10:10:00+00:00")

    def test_daily_schedule_respects_timezone_and_revision(self):
        from autoanime_v3.domain.errors import RevisionConflictError
        from autoanime_v3.services.automation import ScheduleService

        clock = MutableClock()
        service = ScheduleService(self.database, clock=clock)
        schedule = service.create(
            self.profile.id,
            "daily",
            {"time": "18:30"},
            "Asia/Shanghai",
        )
        self.assertEqual(schedule.next_run_at, "2026-07-25T10:30:00+00:00")
        updated = service.update(schedule.id, schedule.revision, {"enabled": False})
        self.assertFalse(updated.enabled)
        self.assertIsNone(updated.next_run_at)
        with self.assertRaises(RevisionConflictError):
            service.update(schedule.id, schedule.revision, {"enabled": True})

    def test_webhook_token_is_hashed_shown_once_and_enforces_scope_and_enabled(self):
        from autoanime_v3.domain.errors import NotFoundError, PathOutsideRootError
        from autoanime_v3.services.automation import WebhookSourceService

        service = WebhookSourceService(self.database)
        created = service.create("qBittorrent", "qbittorrent", self.profile.id)
        self.assertTrue(created.token)
        self.assertNotIn("token", service.list()[0].__dict__)

        connection = sqlite3.connect(str(self.database))
        try:
            stored = connection.execute(
                "SELECT token_hash FROM webhook_sources WHERE id = ?", (created.id,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(stored, hashlib.sha256(created.token.encode("utf-8")).hexdigest())
        self.assertNotEqual(stored, created.token)

        target = self.source / "completed.mkv"
        target.write_bytes(b"media")
        job = service.submit_token(created.token, [target])
        self.assertEqual(job.payload["paths"], [str(target.resolve())])
        with self.assertRaises(PathOutsideRootError):
            service.submit_token(created.token, [self.root / "outside.mkv"])

        updated = service.update(created.id, created.revision, {"enabled": False})
        self.assertFalse(updated.enabled)
        with self.assertRaises(NotFoundError):
            service.submit_token(created.token, [target])

    def test_real_observer_enqueues_stable_target_and_ignores_temporary_suffix(self):
        from autoanime_v3.services.automation import AutomationRuntime

        runtime = AutomationRuntime(
            self.database,
            watch_poll_seconds=0.05,
            observer_reload_seconds=0.05,
        )
        runtime.start()
        try:
            temporary = self.source / "ignored.mkv.!qB"
            temporary.write_bytes(b"partial")
            target = self.source / "ready.mkv"
            target.write_bytes(b"complete")
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and not self.jobs():
                runtime.tick()
                time.sleep(0.05)
            jobs = self.jobs()
            self.assertEqual(len(jobs), 1)
            payload = json.loads(jobs[0]["payload_json"])
            self.assertEqual(payload["paths"], [str(target.resolve())])
        finally:
            runtime.stop()
        self.assertFalse(runtime.is_running)

    def test_targeted_scan_does_not_include_sibling_and_rejects_outside_scope(self):
        from autoanime_v3.domain.errors import PathOutsideRootError
        from autoanime_v3.services.scans import ScanService

        target = self.source / "Target Show S01E01.mkv"
        sibling = self.source / "Sibling Show S01E02.mkv"
        target.write_bytes(b"target")
        sibling.write_bytes(b"sibling")

        outcome = ScanService(self.database).run(self.profile.id, [target])
        self.assertEqual(outcome.discovered_count, 1)
        connection = sqlite3.connect(str(self.database))
        try:
            scanned = [row[0] for row in connection.execute("SELECT path FROM scan_items")]
        finally:
            connection.close()
        self.assertEqual(scanned, [str(target.resolve())])
        with self.assertRaises(PathOutsideRootError):
            ScanService(self.database).run(self.profile.id, [self.root / "outside"])


if __name__ == "__main__":
    unittest.main()
