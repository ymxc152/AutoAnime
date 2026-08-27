import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class OperationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "web.sqlite3"
        self.source = self.root / "downloads"
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
                name="真实执行",
                source_root_id=source_root.id,
                library_root_id=library_root.id,
                mode="link",
            )
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def prepare_plan(self, names):
        for index, name in enumerate(names):
            (self.source / name).write_bytes(("real-file-%s" % index).encode("utf-8") * 1024)
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import ScanService

        outcome = ScanService(self.database).run(self.profile.id)
        return PlanService(self.database).approve(outcome.plan_id)

    def test_rejected_plan_item_is_excluded_from_execution(self):
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import ScanService

        for index, name in enumerate(("测试番 S01E01.mkv", "测试番 S01E02.mkv")):
            (self.source / name).write_bytes(("file-%s" % index).encode("utf-8") * 1024)
        outcome = ScanService(self.database).run(self.profile.id)
        plans = PlanService(self.database)
        draft = plans.get(outcome.plan_id)
        rejected = draft.items[1]
        plans.decide_item(draft.id, rejected.id, "rejected", reason="duplicate")
        approved = plans.approve(draft.id)

        batch = OperationService(self.database).execute(approved.id)

        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.items[0].source_path, approved.items[0].source_path)
        self.assertFalse(Path(rejected.destination_path).exists())

    def test_preflight_checks_entire_batch_before_any_file_change(self):
        from autoanime_v3.domain.errors import StalePlanError
        from autoanime_v3.services.operations import OperationService

        plan = self.prepare_plan(["测试番 S01E01.mkv", "测试番 S01E02.mkv"])
        Path(plan.items[1].source_path).write_bytes(b"changed-after-approval")

        with self.assertRaises(StalePlanError):
            OperationService(self.database).execute(plan.id)
        self.assertFalse(any(path.is_file() for path in self.library.rglob("*")))

    def test_real_hardlink_execution_and_manual_rollback_are_recorded(self):
        from autoanime_v3.services.operations import OperationService

        plan = self.prepare_plan(["测试番 S01E01.mkv"])
        operations = OperationService(self.database)
        batch = operations.execute(plan.id)
        destination = Path(plan.items[0].destination_path)

        self.assertEqual(batch.status, "completed")
        self.assertTrue(destination.is_file())
        self.assertTrue(os.path.samefile(plan.items[0].source_path, destination))
        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.items[0].status, "success")

        rollback_batch = operations.rollback(batch.id)
        self.assertEqual(rollback_batch.status, "completed")
        self.assertFalse(destination.exists())
        self.assertTrue(Path(plan.items[0].source_path).exists())

    def test_partial_automatic_rollback_is_recorded_for_recovery(self):
        from autoanime_v3.executor import ExecutionFailure
        from autoanime_v3.services import operations as operations_module
        from autoanime_v3.services.operations import OperationService

        plan = self.prepare_plan(["测试番 S01E01.mkv"])
        log_path = self.root / "operations" / "partial.jsonl"
        log_path.parent.mkdir()
        log_path.write_text("{}\n", encoding="utf-8")
        applied = {
            "source": plan.items[0].source_path,
            "destination": plan.items[0].destination_path,
            "applied": True,
            "result_sha256": "a" * 64,
        }
        failure = ExecutionFailure(
            "controlled partial rollback",
            log_path=log_path,
            applied_records=[applied],
            rollback_results=[
                {
                    "source": plan.items[0].source_path,
                    "destination": plan.items[0].destination_path,
                    "status": "failed",
                    "error": "destination could not be removed",
                }
            ],
            rollback_errors=["destination could not be removed"],
        )

        with mock.patch.object(operations_module, "execute_plan", side_effect=failure):
            with self.assertRaises(ExecutionFailure):
                OperationService(self.database).execute(plan.id)

        batch = OperationService(self.database).get(1)
        self.assertEqual(batch.status, "failed_partial_rollback")
        self.assertEqual(batch.summary["log_path"], str(log_path))
        self.assertEqual(batch.summary["rollback_errors"], ["destination could not be removed"])
        self.assertEqual(batch.summary["applied_items"], [applied])
        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.items[0].status, "applied")
        self.assertEqual(batch.items[0].compensation_status, "failed")

    def test_execute_approved_subset_leaves_undecided_items_for_later(self):
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import ScanService

        for index, name in enumerate(("测试番 S01E01.mkv", "测试番 S01E02.mkv")):
            (self.source / name).write_bytes(("file-%s" % index).encode("utf-8") * 1024)
        outcome = ScanService(self.database).run(self.profile.id)
        plans = PlanService(self.database)
        draft = plans.get(outcome.plan_id)
        approved = draft.items[0]
        plans.decide_item(draft.id, approved.id, "approved")

        plan, job = plans.enqueue_approved_execution(draft.id)
        self.assertEqual(job.job_type, "execute_plan")
        batch = OperationService(self.database).execute(draft.id)

        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.items[0].source_path, approved.source_path)
        self.assertTrue(Path(approved.destination_path).exists())
        self.assertFalse(Path(draft.items[1].destination_path).exists())
        # The undecided item keeps the plan open for more approvals later.
        self.assertEqual(plans.get(draft.id).status, "ready")

    def test_wholesale_approval_marks_undecided_items_approved(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"real-file" * 1024)
        outcome = ScanService(self.database).run(self.profile.id)
        plans = PlanService(self.database)
        approved = plans.approve(outcome.plan_id)
        self.assertEqual(approved.items[0].decision, "approved")

    def test_successful_execution_syncs_show_and_season_entries(self):
        import sqlite3

        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"real-file" * 1024)
        outcome = ScanService(self.database).run(self.profile.id)
        plans = PlanService(self.database)
        approved = plans.approve(outcome.plan_id)
        OperationService(self.database).execute(approved.id)

        connection = sqlite3.connect(str(self.database))
        try:
            show = connection.execute(
                "SELECT * FROM shows WHERE canonical_title = '测试番'"
            ).fetchone()
            self.assertIsNotNone(show)
            show_id = show[0]
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM seasons WHERE show_id = ?", (show_id,)
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM episodes
                    WHERE season_id = (SELECT id FROM seasons WHERE show_id = ?)
                    """,
                    (show_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM media_assignments
                    WHERE show_id = ? AND source = 'plan_execution'
                    """,
                    (show_id,),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_manual_special_resolution_syncs_locked_title_and_special_episode(self):
        import sqlite3

        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.reviews import ReviewService

        (self.source / "Unknown Show SP03.mkv").write_bytes(b"special-file" * 1024)
        from autoanime_v3.services.scans import ScanService

        ScanService(self.database).run(self.profile.id)
        review = ReviewService(self.database).list_open()[0]
        plan = ReviewService(self.database).resolve(
            review.id,
            {
                "title": "人工锁定特别篇",
                "media_type": "special",
                "season": 0,
                "episode": "SP03",
                "manual_lock": True,
            },
        )
        from autoanime_v3.services.plans import PlanService

        approved = PlanService(self.database).approve(plan.id)
        OperationService(self.database).execute(approved.id)

        connection = sqlite3.connect(str(self.database))
        connection.row_factory = sqlite3.Row
        try:
            show = connection.execute(
                "SELECT * FROM shows WHERE canonical_title = ?",
                ("人工锁定特别篇",),
            ).fetchone()
            self.assertIsNotNone(show)
            self.assertEqual(int(show["title_locked"]), 1)
            season = connection.execute(
                "SELECT * FROM seasons WHERE show_id = ?",
                (show["id"],),
            ).fetchone()
            self.assertEqual(int(season["season_number"]), 0)
            episode = connection.execute(
                "SELECT * FROM episodes WHERE season_id = ?",
                (season["id"],),
            ).fetchone()
            self.assertEqual(episode["episode_number"], "SP03")
            self.assertEqual(episode["episode_type"], "special")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
