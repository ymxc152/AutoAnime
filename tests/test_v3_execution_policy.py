import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


class ExecutionPolicyTests(unittest.TestCase):
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

    def create_profile(self, execution_policy):
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService

        roots = RootService(self.database)
        source_root = roots.create_root("source", self.source)
        library_root = roots.create_root("library", self.library)
        return ProfileService(self.database).create_profile(
            CreateProfile(
                name="execution-policy-test",
                source_root_id=source_root.id,
                library_root_id=library_root.id,
                execution_policy=execution_policy,
                min_confidence=86,
            )
        )

    def scan_safe_file(self, execution_policy):
        from autoanime_v3.services.scans import ScanService

        profile = self.create_profile(execution_policy)
        media = self.source / "测试番 S01E01.mkv"
        media.write_bytes(b"safe-media-content")
        return profile, media, ScanService(self.database).run(profile.id)

    def login(self, client):
        client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "admin", "password": "Correct Horse Battery Staple!42"},
        )
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Correct Horse Battery Staple!42"},
        )
        self.assertEqual(response.status_code, 200)
        return {"X-CSRF-Token": response.json()["csrf_token"]}

    def execute_jobs(self):
        connection = sqlite3.connect(str(self.database))
        try:
            return connection.execute(
                "SELECT id, status, payload_json FROM jobs WHERE job_type = 'execute_plan' ORDER BY id"
            ).fetchall()
        finally:
            connection.close()

    def create_directory_link(self, link, target, junction=False):
        target.mkdir(parents=True, exist_ok=True)
        if junction:
            if os.name != "nt":
                self.skipTest("Windows junction test")
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
            )
            if result.returncode != 0:
                self.skipTest(
                    "Cannot create Windows junction: %s"
                    % result.stderr.decode(errors="replace").strip()
                )
            return
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest("Cannot create directory symlink: %s" % error)

    def remove_directory_link(self, link):
        if not os.path.lexists(str(link)):
            return
        if link.is_symlink():
            link.unlink()
        else:
            os.rmdir(str(link))

    def test_dry_run_api_approval_is_rejected_without_enqueuing_or_touching_files(self):
        from autoanime_v3.api.app import ServerSettings, create_app

        unused_profile, media, outcome = self.scan_safe_file("dry_run")
        original = media.read_bytes()
        client = TestClient(
            create_app(
                ServerSettings(
                    database_path=self.database,
                    data_directory=self.root,
                    secure_cookies=False,
                )
            ),
            client=("127.0.0.1", 50000),
        )
        try:
            response = client.post(
                "/api/v1/plans/%s/approve" % outcome.plan_id,
                headers=self.login(client),
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "execution_policy_forbidden")
        self.assertEqual(self.execute_jobs(), [])
        self.assertEqual(media.read_bytes(), original)
        self.assertEqual(list(self.library.rglob("*")), [])

    def test_dry_run_operation_service_rejects_even_an_already_approved_plan(self):
        from autoanime_v3.domain.errors import ExecutionPolicyError
        from autoanime_v3.services.operations import OperationService

        unused_profile, media, outcome = self.scan_safe_file("dry_run")
        connection = sqlite3.connect(str(self.database))
        try:
            connection.execute("UPDATE plans SET status = 'approved' WHERE id = ?", (outcome.plan_id,))
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(ExecutionPolicyError) as raised:
            OperationService(self.database, self.root / "operations").execute(outcome.plan_id)
        self.assertEqual(raised.exception.code, "execution_policy_forbidden")
        self.assertTrue(media.exists())
        self.assertEqual(list(self.library.rglob("*")), [])

    def test_worker_execution_rejects_profile_revision_changed_after_approval(self):
        from autoanime_v3.domain.errors import StalePlanError
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.profiles import ProfileService

        profile, media, outcome = self.scan_safe_file("review_all")
        plan, job = PlanService(self.database).approve_and_enqueue(outcome.plan_id)
        destination = Path(plan.items[0].destination_path)
        self.assertEqual(job.status, "queued")
        ProfileService(self.database).update_profile(
            profile.id,
            profile.revision,
            {"min_confidence": 85},
        )

        with self.assertRaises(StalePlanError):
            OperationService(self.database, self.root / "operations").execute(plan.id)

        self.assertTrue(media.exists())
        self.assertFalse(destination.exists())
        self.assertFalse(any(path.is_file() for path in self.library.rglob("*")))
        self.assertEqual(PlanService(self.database).get(plan.id).status, "stale")

    def test_auto_approval_rejects_a_destination_parent_symlink_outside_library(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import CoreScanAdapter, ScanService

        profile = self.create_profile("auto_apply_safe")
        (self.source / "测试番 S01E01.mkv").write_bytes(b"safe-media-content")
        unused_rule, unused_resolutions, entries = CoreScanAdapter(self.database).analyze(
            self.source, self.library, 0.86
        )
        first_component = entries[0].destination.relative_to(self.library).parts[0]
        link = self.library / first_component
        outside = self.root / "outside-symlink"
        self.create_directory_link(link, outside)
        try:
            outcome = ScanService(self.database).run(profile.id)
            plan = PlanService(self.database).get(outcome.plan_id)
            self.assertNotEqual(plan.status, "approved")
            self.assertEqual(self.execute_jobs(), [])
        finally:
            self.remove_directory_link(link)
        self.assertFalse(any(path.is_file() for path in outside.rglob("*")))

    def test_auto_approval_rejects_a_windows_junction_outside_library(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import CoreScanAdapter, ScanService

        profile = self.create_profile("auto_apply_safe")
        (self.source / "测试番 S01E01.mkv").write_bytes(b"safe-media-content")
        unused_rule, unused_resolutions, entries = CoreScanAdapter(self.database).analyze(
            self.source, self.library, 0.86
        )
        first_component = entries[0].destination.relative_to(self.library).parts[0]
        link = self.library / first_component
        outside = self.root / "outside-auto-junction"
        self.create_directory_link(link, outside, junction=True)
        try:
            outcome = ScanService(self.database).run(profile.id)
            plan = PlanService(self.database).get(outcome.plan_id)
            self.assertNotEqual(plan.status, "approved")
            self.assertEqual(self.execute_jobs(), [])
        finally:
            self.remove_directory_link(link)
        self.assertFalse(any(path.is_file() for path in outside.rglob("*")))

    def test_execution_rejects_a_windows_junction_inserted_after_approval(self):
        from autoanime_v3.domain.errors import PlanConflictError
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService

        unused_profile, media, outcome = self.scan_safe_file("review_all")
        plan, unused_job = PlanService(self.database).approve_and_enqueue(outcome.plan_id)
        destination = Path(plan.items[0].destination_path)
        relative = destination.relative_to(self.library)
        link = self.library / relative.parts[0]
        outside = self.root / "outside-junction"
        self.create_directory_link(link, outside, junction=True)
        try:
            with self.assertRaises(PlanConflictError):
                OperationService(self.database, self.root / "operations").execute(plan.id)
        finally:
            self.remove_directory_link(link)
        self.assertTrue(media.exists())
        self.assertFalse(any(path.is_file() for path in outside.rglob("*")))

    def test_executor_rechecks_destination_after_claim_for_all_file_modes(self):
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.executor import ExecutionError
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        for mode in ("link", "copy", "move"):
            with self.subTest(mode=mode):
                case_root = self.root / ("claim-race-" + mode)
                source = case_root / "source"
                library = case_root / "library"
                outside = case_root / "outside"
                source.mkdir(parents=True)
                library.mkdir()
                database = case_root / "web.sqlite3"
                roots = RootService(database)
                source_root = roots.create_root("source", source)
                library_root = roots.create_root("library", library)
                profile = ProfileService(database).create_profile(
                    CreateProfile(
                        name="claim-race-" + mode,
                        source_root_id=source_root.id,
                        library_root_id=library_root.id,
                        mode=mode,
                        execution_policy="review_all",
                    )
                )
                media = source / "测试番 S01E01.mkv"
                media.write_bytes(b"safe-media-content")
                outcome = ScanService(database).run(profile.id)
                plan, unused_job = PlanService(database).approve_and_enqueue(outcome.plan_id)
                destination = Path(plan.items[0].destination_path)
                relative = destination.relative_to(library)
                link = library / relative.parts[0]

                outer = self

                class JunctionAfterClaimOperationService(OperationService):
                    def _claim_execution(inner_self, plan_id, requested_by, rows):
                        batch_id = super()._claim_execution(plan_id, requested_by, rows)
                        outer.create_directory_link(
                            link,
                            outside,
                            junction=os.name == "nt",
                        )
                        return batch_id

                try:
                    with self.assertRaises(ExecutionError):
                        JunctionAfterClaimOperationService(
                            database, case_root / "operations"
                        ).execute(plan.id)
                finally:
                    self.remove_directory_link(link)

                self.assertTrue(media.exists())
                self.assertFalse(any(path.is_file() for path in outside.rglob("*")))
                connection = sqlite3.connect(str(database))
                try:
                    batch = connection.execute(
                        "SELECT status FROM operation_batches WHERE plan_id = ?",
                        (plan.id,),
                    ).fetchone()
                    self.assertEqual(batch[0], "failed_rolled_back")
                finally:
                    connection.close()

    def test_execution_claim_rechecks_policy_after_preflight(self):
        from autoanime_v3.domain.errors import ExecutionPolicyError
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.profiles import ProfileService

        profile, media, outcome = self.scan_safe_file("review_all")
        plan, unused_job = PlanService(self.database).approve_and_enqueue(outcome.plan_id)
        destination = Path(plan.items[0].destination_path)

        class PolicyChangingOperationService(OperationService):
            def _preflight(inner_self, checked_plan, rows):
                prepared = super()._preflight(checked_plan, rows)
                ProfileService(self.database).update_profile(
                    profile.id,
                    profile.revision,
                    {"execution_policy": "dry_run"},
                )
                return prepared

        with self.assertRaises(ExecutionPolicyError):
            PolicyChangingOperationService(
                self.database, self.root / "operations"
            ).execute(plan.id)
        self.assertTrue(media.exists())
        self.assertFalse(destination.exists())
        connection = sqlite3.connect(str(self.database))
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM operation_batches").fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_concurrent_execute_calls_only_one_claims_the_approved_plan(self):
        from autoanime_v3.domain.errors import InvalidStateError
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService

        unused_profile, unused_media, outcome = self.scan_safe_file("review_all")
        plan, unused_job = PlanService(self.database).approve_and_enqueue(outcome.plan_id)
        original_preflight = OperationService._preflight
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def synchronized_preflight(service, checked_plan, rows):
            prepared = original_preflight(service, checked_plan, rows)
            barrier.wait(timeout=10)
            return prepared

        def execute_once():
            try:
                results.append(
                    OperationService(self.database, self.root / "operations").execute(plan.id)
                )
            except Exception as error:
                errors.append(error)

        with mock.patch.object(OperationService, "_preflight", new=synchronized_preflight):
            threads = [threading.Thread(target=execute_once) for unused in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], InvalidStateError)
        connection = sqlite3.connect(str(self.database))
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM operation_batches").fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_rollback_api_validates_eligibility_before_enqueuing(self):
        from autoanime_v3.api.app import ServerSettings, create_app
        from autoanime_v3.db.migrations import run_migrations

        log_path = self.root / "rollback-source.jsonl"
        log_path.write_text("", encoding="utf-8")
        missing_log = self.root / "missing-operation-log.jsonl"
        run_migrations(self.database)
        connection = sqlite3.connect(str(self.database))
        try:
            running_id = connection.execute(
                "INSERT INTO operation_batches(kind, status, summary_json) VALUES ('execute', 'running', '{}')"
            ).lastrowid
            missing_log_id = connection.execute(
                "INSERT INTO operation_batches(kind, status, summary_json) VALUES ('execute', 'completed', ?)",
                ('{"log_path": "%s"}' % str(missing_log).replace("\\", "\\\\"),),
            ).lastrowid
            rolled_back_id = connection.execute(
                "INSERT INTO operation_batches(kind, status, summary_json) VALUES ('execute', 'completed', ?)",
                ('{"log_path": "%s"}' % str(log_path).replace("\\", "\\\\"),),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO operation_batches(parent_batch_id, kind, status, summary_json)
                VALUES (?, 'manual_rollback', 'completed', '{}')
                """,
                (rolled_back_id,),
            )
            eligible_id = connection.execute(
                "INSERT INTO operation_batches(kind, status, summary_json) VALUES ('execute', 'completed', ?)",
                ('{"log_path": "%s"}' % str(log_path).replace("\\", "\\\\"),),
            ).lastrowid
            connection.commit()
        finally:
            connection.close()

        client = TestClient(
            create_app(
                ServerSettings(
                    database_path=self.database,
                    data_directory=self.root,
                    secure_cookies=False,
                )
            ),
            client=("127.0.0.1", 50000),
        )
        try:
            headers = self.login(client)
            missing = client.post("/api/v1/operations/999999/rollback", headers=headers)
            running = client.post(
                "/api/v1/operations/%s/rollback" % running_id, headers=headers
            )
            no_log = client.post(
                "/api/v1/operations/%s/rollback" % missing_log_id, headers=headers
            )
            rolled_back = client.post(
                "/api/v1/operations/%s/rollback" % rolled_back_id, headers=headers
            )
            eligible = client.post(
                "/api/v1/operations/%s/rollback" % eligible_id, headers=headers
            )
        finally:
            client.close()

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "not_found")
        self.assertEqual(running.status_code, 409)
        self.assertEqual(running.json()["code"], "invalid_state")
        self.assertEqual(no_log.status_code, 404)
        self.assertEqual(no_log.json()["code"], "not_found")
        self.assertEqual(rolled_back.status_code, 409)
        self.assertEqual(rolled_back.json()["code"], "invalid_state")
        self.assertEqual(eligible.status_code, 202)
        self.assertEqual(eligible.json()["job_type"], "rollback_operation")
        connection = sqlite3.connect(str(self.database))
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE job_type = 'rollback_operation'"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_concurrent_rollback_workers_only_one_claims_the_completed_batch(self):
        from autoanime_v3.domain.errors import InvalidStateError
        from autoanime_v3.services.operations import OperationService

        unused_profile, unused_media, outcome = self.scan_safe_file("review_all")
        from autoanime_v3.services.plans import PlanService

        plan, unused_job = PlanService(self.database).approve_and_enqueue(outcome.plan_id)
        batch = OperationService(self.database, self.root / "operations").execute(plan.id)
        barrier = threading.Barrier(2)
        results = []
        errors = []

        class SynchronizedRollbackService(OperationService):
            def validate_rollback(inner_self, batch_id):
                original = super().validate_rollback(batch_id)
                barrier.wait(timeout=10)
                return original

        def rollback_once():
            try:
                results.append(
                    SynchronizedRollbackService(
                        self.database, self.root / "operations"
                    ).rollback(batch.id)
                )
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=rollback_once) for unused in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], InvalidStateError)
        connection = sqlite3.connect(str(self.database))
        try:
            children = connection.execute(
                """
                SELECT status FROM operation_batches
                WHERE parent_batch_id = ? AND kind = 'manual_rollback'
                """,
                (batch.id,),
            ).fetchall()
            self.assertEqual(children, [("completed",)])
        finally:
            connection.close()

    def test_review_all_waits_for_api_approval_then_enqueues_once(self):
        from autoanime_v3.api.app import ServerSettings, create_app

        unused_profile, unused_media, outcome = self.scan_safe_file("review_all")
        self.assertEqual(self.execute_jobs(), [])
        client = TestClient(
            create_app(
                ServerSettings(
                    database_path=self.database,
                    data_directory=self.root,
                    secure_cookies=False,
                )
            ),
            client=("127.0.0.1", 50000),
        )
        try:
            headers = self.login(client)
            first = client.post("/api/v1/plans/%s/approve" % outcome.plan_id, headers=headers)
            second = client.post("/api/v1/plans/%s/approve" % outcome.plan_id, headers=headers)
        finally:
            client.close()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["job"]["id"], second.json()["job"]["id"])
        self.assertEqual(len(self.execute_jobs()), 1)

    def test_public_plan_approve_cannot_create_an_approved_plan_without_a_job(self):
        from autoanime_v3.services.plans import PlanService

        unused_profile, unused_media, outcome = self.scan_safe_file("review_all")

        plan = PlanService(self.database).approve(outcome.plan_id)

        self.assertEqual(plan.status, "approved")
        self.assertEqual(len(self.execute_jobs()), 1)

    def test_auto_apply_safe_approves_and_enqueues_a_ready_safe_plan(self):
        from autoanime_v3.services.plans import PlanService

        unused_profile, unused_media, outcome = self.scan_safe_file("auto_apply_safe")

        plan = PlanService(self.database).get(outcome.plan_id)
        self.assertEqual(outcome.plan_status, "approved")
        self.assertEqual(plan.status, "approved")
        self.assertTrue(plan.items)
        self.assertTrue(all(item.risk_level == "normal" for item in plan.items))
        self.assertTrue(any(item.action not in {"skip", "conflict"} for item in plan.items))
        jobs = self.execute_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertIn('"plan_id": %s' % outcome.plan_id, jobs[0][2])

    def test_auto_apply_safe_does_not_enqueue_an_empty_ready_plan(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import ScanService

        profile = self.create_profile("auto_apply_safe")

        outcome = ScanService(self.database).run(profile.id)

        self.assertEqual(PlanService(self.database).get(outcome.plan_id).status, "ready")
        self.assertEqual(self.execute_jobs(), [])

    def test_auto_apply_safe_leaves_a_review_plan_unapproved_and_unqueued(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import ScanService

        profile = self.create_profile("auto_apply_safe")
        (self.source / "Unknown Show - 02.mkv").write_bytes(b"needs-review")

        outcome = ScanService(self.database).run(profile.id)

        self.assertEqual(PlanService(self.database).get(outcome.plan_id).status, "draft")
        self.assertEqual(self.execute_jobs(), [])

    def test_resolving_the_last_review_auto_applies_the_new_safe_plan(self):
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.scans import ScanService

        profile = self.create_profile("auto_apply_safe")
        media = self.source / "Unknown Show - 02.mkv"
        media.write_bytes(b"needs-review")
        ScanService(self.database).run(profile.id)
        reviews = ReviewService(self.database)

        plan = reviews.resolve(
            reviews.list_open()[0].id,
            {"title": "人工确认番剧", "season": 1, "episode": 2, "is_movie": False},
        )

        self.assertEqual(plan.status, "approved")
        self.assertEqual(len(self.execute_jobs()), 1)
        self.assertTrue(media.exists())
        self.assertFalse(any(path.is_file() for path in self.library.rglob("*")))

    def test_auto_apply_safe_leaves_a_conflicting_plan_unapproved_and_unqueued(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import CoreScanAdapter, ScanService

        profile = self.create_profile("auto_apply_safe")
        (self.source / "测试番 S01E01.mkv").write_bytes(b"safe-media-content")
        unused_rule_version, unused_resolutions, entries = CoreScanAdapter(
            self.database
        ).analyze(self.source, self.library, 0.86)
        destination = entries[0].destination
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"occupied")

        outcome = ScanService(self.database).run(profile.id)

        self.assertNotEqual(PlanService(self.database).get(outcome.plan_id).status, "approved")
        self.assertEqual(self.execute_jobs(), [])
        self.assertEqual(destination.read_bytes(), b"occupied")

    def test_auto_apply_safe_does_not_approve_a_plan_staled_during_analysis(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.scans import CoreScanAdapter, ScanService

        profile = self.create_profile("auto_apply_safe")
        (self.source / "测试番 S01E01.mkv").write_bytes(b"safe-media-content")
        delegate = CoreScanAdapter(self.database)

        class ProfileChangingAdapter:
            def analyze(inner_self, source, library, min_confidence):
                ProfileService(self.database).update_profile(
                    profile.id,
                    profile.revision,
                    {"min_confidence": 85},
                )
                return delegate.analyze(source, library, min_confidence)

        outcome = ScanService(self.database, adapter=ProfileChangingAdapter()).run(profile.id)

        self.assertEqual(PlanService(self.database).get(outcome.plan_id).status, "stale")
        self.assertEqual(self.execute_jobs(), [])

    def test_approve_and_enqueue_preserves_not_found_for_a_missing_plan(self):
        from autoanime_v3.domain.errors import NotFoundError
        from autoanime_v3.services.plans import PlanService

        with self.assertRaises(NotFoundError):
            PlanService(self.database).approve_and_enqueue(999999)
        self.assertEqual(self.execute_jobs(), [])


if __name__ == "__main__":
    unittest.main()
