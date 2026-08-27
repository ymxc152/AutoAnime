import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


def _worker(database, operations_dir):
    from autoanime_v3.jobs.queue import JobQueue
    from autoanime_v3.jobs.worker import Worker
    from autoanime_v3.services.operations import OperationService
    from autoanime_v3.services.scans import ScanService

    queue = JobQueue(database)

    def scan_handler(job):
        ScanService(database).run(
            int(job.payload["profile_id"]),
            job.payload.get("paths") or None,
        )

    def execute_handler(job):
        OperationService(database, operations_dir).execute(int(job.payload["plan_id"]))

    def rollback_handler(job):
        OperationService(database, operations_dir).rollback(int(job.payload["batch_id"]))

    return queue, Worker(
        "gap-test-worker",
        queue,
        {
            "scan": scan_handler,
            "execute_plan": execute_handler,
            "rollback_operation": rollback_handler,
        },
    )


class WebhookWorkerChainTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "web.sqlite3"
        self.source = self.root / "downloads"
        self.library = self.root / "library"
        self.source.mkdir()
        self.library.mkdir()
        from autoanime_v3.api.app import ServerSettings, create_app

        self.client = TestClient(
            create_app(
                ServerSettings(
                    database_path=self.database,
                    data_directory=self.root,
                    secure_cookies=False,
                )
            ),
            client=("127.0.0.1", 50000),
        )

    def tearDown(self):
        self.client.close()
        self.temporary_directory.cleanup()

    def login(self):
        from autoanime_v3.services.auth import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME

        response = self.client.post(
            "/api/v1/auth/login",
            json={"username": DEFAULT_ADMIN_USERNAME, "password": DEFAULT_ADMIN_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        return {"X-CSRF-Token": response.json()["csrf_token"]}

    def test_webhook_savepath_runs_worker_hardlink_and_rollback(self):
        headers = self.login()
        source_id = self.client.post(
            "/api/v1/roots", json={"kind": "source", "path": str(self.source)}, headers=headers
        ).json()["id"]
        library_id = self.client.post(
            "/api/v1/roots", json={"kind": "library", "path": str(self.library)}, headers=headers
        ).json()["id"]
        profile = self.client.post(
            "/api/v1/profiles",
            json={
                "name": "webhook-chain",
                "source_root_id": source_id,
                "library_root_id": library_id,
                "mode": "link",
                "execution_policy": "review_all",
                "min_confidence": 80,
            },
            headers=headers,
        ).json()
        created = self.client.post(
            "/api/v1/webhook-sources",
            json={"name": "qBittorrent", "downloader": "qbittorrent", "profile_id": profile["id"]},
            headers=headers,
        )
        self.assertEqual(created.status_code, 201)
        patched = self.client.patch(
            "/api/v1/profiles/%s" % profile["id"],
            json={
                "revision": profile["revision"],
                "patch": {"execution_policy": "auto_apply_safe", "mode": "link"},
            },
            headers=headers,
        )
        self.assertEqual(patched.status_code, 200)

        media = self.source / "测试番 S01E01.mkv"
        media.write_bytes(b"webhook-chain-media")
        hooked = self.client.post(
            "/api/v1/hooks/downloaders/%s" % created.json()["token"],
            json={"savePath": str(media)},
        )
        self.assertEqual(hooked.status_code, 202)
        self.assertEqual(hooked.json()["payload"]["trigger"], "webhook")

        linux = self.client.post(
            "/api/v1/hooks/downloaders/%s" % created.json()["token"],
            json={"path": "/downloads/outside.mkv"},
        )
        self.assertEqual(linux.status_code, 409)
        self.assertEqual(linux.json()["code"], "path_outside_root")

        queue, worker = _worker(self.database, self.root / "operations")
        scan_job = worker.run_once(lease_seconds=30)
        self.assertIsNotNone(scan_job)
        self.assertEqual(scan_job.job_type, "scan")
        self.assertEqual(scan_job.status, "succeeded")

        execute_job = worker.run_once(lease_seconds=30)
        self.assertIsNotNone(execute_job)
        self.assertEqual(execute_job.job_type, "execute_plan")
        self.assertEqual(execute_job.status, "succeeded")

        destinations = [path for path in self.library.rglob("*") if path.is_file()]
        self.assertEqual(len(destinations), 1)
        self.assertTrue(os.path.samefile(media, destinations[0]))
        self.assertTrue(media.is_file())

        connection = sqlite3.connect(str(self.database))
        try:
            batch_id = connection.execute(
                "SELECT id FROM operation_batches WHERE kind = 'execute' AND status = 'completed' ORDER BY id DESC"
            ).fetchone()[0]
        finally:
            connection.close()
        from autoanime_v3.services.operations import OperationService

        OperationService(self.database, self.root / "operations").rollback(batch_id)
        self.assertFalse(destinations[0].exists())
        self.assertTrue(media.is_file())
        self.assertEqual(queue.enqueue("scan", {"profile_id": profile["id"]}).job_type, "scan")


class MemoryThenRescanTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "web.sqlite3"
        self.source = self.root / "downloads"
        self.library = self.root / "library"
        self.source.mkdir()
        self.library.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_agent_apply_writes_memory_and_rescan_skips_new_review(self):
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.agent_chat import AgentChatService
        from autoanime_v3.services.memory import ShowMemoryService
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        roots = RootService(self.database)
        source_root = roots.create_root("source", self.source)
        library_root = roots.create_root("library", self.library)
        profile = ProfileService(self.database).create_profile(
            CreateProfile(
                name="memory-rescan",
                source_root_id=source_root.id,
                library_root_id=library_root.id,
                mode="link",
                execution_policy="review_all",
                min_confidence=86,
            )
        )
        media = self.source / "Unknown Show S01E01.mkv"
        media.write_bytes(b"needs-review-then-memory")
        first = ScanService(self.database).run(profile.id)
        self.assertGreater(first.review_count, 0)
        review = ReviewService(self.database).list_open()[0]
        chat = AgentChatService(
            self.database,
            chat_completion=lambda unused_messages: (
                '{"title":"人工确认番","media_type":"episode","season":1,"episode":1,'
                '"destination":"D:/invented","action":"move","reason":"gap"}'
            ),
        )
        session = chat.open_session("review", review.id)
        updated = chat.add_message(session["id"], "确认中文名")
        self.assertEqual(updated["proposal"]["title"], "人工确认番")
        self.assertNotIn("destination", updated["proposal"])
        applied = chat.apply(session["id"])
        self.assertTrue(applied["applied"])
        memory = ShowMemoryService(self.database).list()
        self.assertTrue(any(item["canonical_title"] == "人工确认番" for item in memory))
        self.assertTrue(any(item["source"] == "review" for item in memory))

        second = ScanService(self.database).run(profile.id)
        self.assertEqual(ReviewService(self.database).list_open(), ())
        self.assertEqual(second.review_count, 0)


if __name__ == "__main__":
    unittest.main()
