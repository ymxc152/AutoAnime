import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class ActiveRuleIntegrationTests(unittest.TestCase):
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
                name="活动规则",
                source_root_id=source_root.id,
                library_root_id=library_root.id,
                mode="link",
            )
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def activate(self, rule_set, document):
        from autoanime_v3.services.rules import RuleService

        service = RuleService(self.database)
        revision = service.create_revision(rule_set.id, document)
        return service.activate(service.validate(revision.id).id)

    def set_active_revision_without_staling_plans(self, rule_set_id, revision_id):
        connection = sqlite3.connect(str(self.database))
        try:
            connection.execute(
                "UPDATE rule_sets SET active_revision_id = ? WHERE id = ?",
                (revision_id, rule_set_id),
            )
            connection.commit()
        finally:
            connection.close()

    def set_execution_policy_without_revision(self, execution_policy):
        connection = sqlite3.connect(str(self.database))
        try:
            connection.execute(
                "UPDATE scan_profiles SET execution_policy = ? WHERE id = ?",
                (execution_policy, self.profile.id),
            )
            connection.commit()
        finally:
            connection.close()

    def execute_job_count(self):
        connection = sqlite3.connect(str(self.database))
        try:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE job_type = 'execute_plan'"
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def latest_identification(self):
        connection = sqlite3.connect(str(self.database))
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(
                """
                SELECT title, accepted, decision_fingerprint, rule_version
                FROM identification_results ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()

    def test_multiple_active_sets_merge_by_set_id_with_later_values_winning(self):
        from autoanime_v3.services.rules import RuleService, canonical_document

        service = RuleService(self.database)
        first_set = service.create_set("第一组")
        second_set = service.create_set("第二组")
        first = self.activate(
            first_set,
            {
                "aliases": {"Shared": "第一标题", "First Only": "第一独有"},
                "season_layouts": {"共同标题": [12]},
                "episode_defaults": {"Shared PV": [0, 1]},
                "season_defaults": {"Shared": 1},
            },
        )
        second = self.activate(
            second_set,
            {
                "aliases": {"Shared": "第二标题", "Second Only": "第二独有"},
                "season_layouts": {"共同标题": [12, 12]},
                "episode_defaults": {"Shared PV": [0, 2]},
                "season_defaults": {"Shared": 2},
            },
        )

        active = service.get_active()

        self.assertEqual(active.revision_ids, (first.id, second.id))
        self.assertEqual(active.document["aliases"]["Shared"], "第二标题")
        self.assertEqual(active.document["aliases"]["First Only"], "第一独有")
        self.assertEqual(active.document["aliases"]["Second Only"], "第二独有")
        self.assertEqual(active.document["season_layouts"]["共同标题"], [12, 12])
        self.assertEqual(active.document["episode_defaults"]["Shared PV"], [0, 2])
        self.assertEqual(active.document["season_defaults"]["Shared"], 2)
        self.assertEqual(
            active.content_hash,
            hashlib.sha256(canonical_document(active.document).encode("utf-8")).hexdigest(),
        )
        self.assertEqual(service.get_active(), active)

    def test_no_active_rules_keep_builtin_aliases_with_a_deterministic_version(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        (self.source / "Sousou no Frieren S01E01.mkv").write_bytes(b"builtin-alias")
        active = RuleService(self.database).get_active()
        outcome = ScanService(self.database).run(self.profile.id)
        result = self.latest_identification()

        self.assertEqual(active.revision_ids, ())
        self.assertEqual(result["title"], "葬送的芙莉莲")
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["rule_version"], active.content_hash)
        self.assertEqual(PlanService(self.database).get(outcome.plan_id).rule_version, active.content_hash)

    def test_activating_alias_changes_real_scan_resolution_and_decision_version(self):
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        (self.source / "Runtime Alias S01E03.mkv").write_bytes(b"runtime-alias")
        scanner = ScanService(self.database)
        scanner.run(self.profile.id)
        before = self.latest_identification()

        service = RuleService(self.database)
        rule_set = service.create_set("扫描别名")
        self.activate(rule_set, {"aliases": {"Runtime Alias": "运行时番剧"}})
        active = service.get_active()
        outcome = scanner.run(self.profile.id)
        after = self.latest_identification()

        self.assertEqual(after["title"], "运行时番剧")
        self.assertEqual(after["accepted"], 1)
        self.assertEqual(after["rule_version"], active.content_hash)
        self.assertEqual(outcome.review_count, 0)
        self.assertNotEqual(after["decision_fingerprint"], before["decision_fingerprint"])
        from autoanime_v3.services.plans import PlanService

        self.assertEqual(PlanService(self.database).get(outcome.plan_id).rule_version, active.content_hash)

    def test_rule_switch_after_analysis_persists_a_stale_plan_and_matching_outcome(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import CoreScanAdapter, ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"rule-race")
        rules = RuleService(self.database)
        rule_set = rules.create_set("扫描竞态规则")
        self.activate(rule_set, {"aliases": {"Versioned": "旧标题"}})
        analyzed_version = rules.get_active().content_hash
        delegate = CoreScanAdapter(self.database)
        test_case = self

        class RuleChangingAdapter:
            def analyze(inner_self, source, library, min_confidence):
                result = delegate.analyze(source, library, min_confidence)
                test_case.activate(rule_set, {"aliases": {"Versioned": "新标题"}})
                return result

        outcome = ScanService(self.database, adapter=RuleChangingAdapter()).run(self.profile.id)
        plan = PlanService(self.database).get(outcome.plan_id)

        self.assertEqual(plan.rule_version, analyzed_version)
        self.assertNotEqual(plan.rule_version, rules.get_active().content_hash)
        self.assertEqual(plan.status, "stale")
        self.assertEqual(outcome.plan_status, plan.status)

    def test_rule_switch_after_analysis_is_not_auto_applied_and_reports_db_status(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import CoreScanAdapter, ScanService

        self.set_execution_policy_without_revision("auto_apply_safe")
        (self.source / "测试番 S01E01.mkv").write_bytes(b"rule-race-auto")
        rules = RuleService(self.database)
        rule_set = rules.create_set("自动应用竞态规则")
        self.activate(rule_set, {"aliases": {"Versioned": "旧标题"}})
        delegate = CoreScanAdapter(self.database)
        test_case = self

        class RuleChangingAdapter:
            def analyze(inner_self, source, library, min_confidence):
                result = delegate.analyze(source, library, min_confidence)
                test_case.activate(rule_set, {"aliases": {"Versioned": "新标题"}})
                return result

        outcome = ScanService(self.database, adapter=RuleChangingAdapter()).run(self.profile.id)
        plan = PlanService(self.database).get(outcome.plan_id)

        self.assertEqual(plan.status, "stale")
        self.assertEqual(outcome.plan_status, plan.status)
        self.assertEqual(self.execute_job_count(), 0)

    def test_explicit_wrong_rule_version_stales_approve_even_when_db_matches(self):
        from autoanime_v3.domain.errors import StalePlanError
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"approve-caller-version")
        outcome = ScanService(self.database).run(self.profile.id)
        plans = PlanService(self.database)

        with self.assertRaises(StalePlanError):
            plans.approve(
                outcome.plan_id,
                current_rule_version="caller-supplied-wrong-version",
            )

        self.assertEqual(plans.get(outcome.plan_id).status, "stale")

    def test_explicit_wrong_rule_version_stales_approve_and_enqueue_even_when_db_matches(self):
        from autoanime_v3.domain.errors import StalePlanError
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"caller-version")
        outcome = ScanService(self.database).run(self.profile.id)
        plans = PlanService(self.database)
        plan = plans.get(outcome.plan_id)
        self.assertEqual(plan.rule_version, RuleService(self.database).get_active().content_hash)

        with self.assertRaises(StalePlanError):
            plans.approve_and_enqueue(
                plan.id,
                current_rule_version="caller-supplied-wrong-version",
            )

        self.assertEqual(plans.get(plan.id).status, "stale")

    def test_explicit_wrong_rule_version_stales_auto_apply_safe_even_when_db_matches(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"automatic-caller-version")
        outcome = ScanService(self.database).run(self.profile.id)
        self.set_execution_policy_without_revision("auto_apply_safe")
        plans = PlanService(self.database)
        plan = plans.get(outcome.plan_id)
        self.assertEqual(plan.rule_version, RuleService(self.database).get_active().content_hash)

        automatic = plans.auto_apply_safe(
            plan.id,
            current_rule_version="caller-supplied-wrong-version",
        )

        self.assertIsNone(automatic)
        self.assertEqual(plans.get(plan.id).status, "stale")

    def test_correct_caller_version_still_stales_approval_when_db_active_rules_changed(self):
        from autoanime_v3.domain.errors import StalePlanError
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        rules = RuleService(self.database)
        rule_set = rules.create_set("数据库版本优先")
        self.activate(rule_set, {"aliases": {"Versioned": "旧标题"}})
        (self.source / "测试番 S01E01.mkv").write_bytes(b"database-version")
        outcome = ScanService(self.database).run(self.profile.id)
        plans = PlanService(self.database)
        plan = plans.get(outcome.plan_id)
        newer = rules.create_revision(rule_set.id, {"aliases": {"Versioned": "新标题"}})
        newer = rules.validate(newer.id)
        self.set_active_revision_without_staling_plans(rule_set.id, newer.id)
        self.assertEqual(plans.get(plan.id).status, "ready")

        with self.assertRaises(StalePlanError):
            plans.approve(plan.id, current_rule_version=plan.rule_version)

        self.assertEqual(plans.get(plan.id).status, "stale")

    def test_correct_caller_version_still_stales_enqueue_when_db_active_rules_changed(self):
        from autoanime_v3.domain.errors import StalePlanError
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        rules = RuleService(self.database)
        rule_set = rules.create_set("入队数据库版本优先")
        self.activate(rule_set, {"aliases": {"Versioned": "旧标题"}})
        (self.source / "测试番 S01E01.mkv").write_bytes(b"enqueue-database-version")
        outcome = ScanService(self.database).run(self.profile.id)
        plans = PlanService(self.database)
        plan = plans.get(outcome.plan_id)
        newer = rules.validate(
            rules.create_revision(rule_set.id, {"aliases": {"Versioned": "新标题"}}).id
        )
        self.set_active_revision_without_staling_plans(rule_set.id, newer.id)

        with self.assertRaises(StalePlanError):
            plans.approve_and_enqueue(
                plan.id,
                current_rule_version=plan.rule_version,
            )

        self.assertEqual(plans.get(plan.id).status, "stale")

    def test_correct_caller_version_still_stales_auto_apply_when_db_active_rules_changed(self):
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        rules = RuleService(self.database)
        rule_set = rules.create_set("自动应用数据库版本优先")
        self.activate(rule_set, {"aliases": {"Versioned": "旧标题"}})
        (self.source / "测试番 S01E01.mkv").write_bytes(b"auto-database-version")
        outcome = ScanService(self.database).run(self.profile.id)
        self.set_execution_policy_without_revision("auto_apply_safe")
        plans = PlanService(self.database)
        plan = plans.get(outcome.plan_id)
        newer = rules.validate(
            rules.create_revision(rule_set.id, {"aliases": {"Versioned": "新标题"}}).id
        )
        self.set_active_revision_without_staling_plans(rule_set.id, newer.id)

        automatic = plans.auto_apply_safe(
            plan.id,
            current_rule_version=plan.rule_version,
        )

        self.assertIsNone(automatic)
        self.assertEqual(plans.get(plan.id).status, "stale")
        self.assertEqual(self.execute_job_count(), 0)

    def test_activating_new_revision_marks_old_ready_plan_stale_and_approval_fails(self):
        from autoanime_v3.domain.errors import StalePlanError
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"ready-plan")
        outcome = ScanService(self.database).run(self.profile.id)
        plans = PlanService(self.database)
        self.assertEqual(plans.get(outcome.plan_id).status, "ready")

        rules = RuleService(self.database)
        rule_set = rules.create_set("计划规则")
        self.activate(rule_set, {"aliases": {"unused": "未使用"}})

        self.assertEqual(plans.get(outcome.plan_id).status, "stale")
        with self.assertRaises(StalePlanError):
            plans.approve(outcome.plan_id)
        self.assertEqual(plans.get(outcome.plan_id).status, "stale")

    def test_rule_switch_after_approval_prevents_execution_without_file_changes(self):
        from autoanime_v3.domain.errors import StalePlanError
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"approved-plan")
        rules = RuleService(self.database)
        rule_set = rules.create_set("执行规则")
        self.activate(rule_set, {"aliases": {"Versioned": "旧标题"}})
        outcome = ScanService(self.database).run(self.profile.id)
        approved = PlanService(self.database).approve(outcome.plan_id)
        destination = Path(approved.items[0].destination_path)

        self.activate(rule_set, {"aliases": {"Versioned": "新标题"}})

        with self.assertRaises(StalePlanError):
            OperationService(self.database).execute(approved.id)
        self.assertFalse(destination.exists())
        self.assertEqual(PlanService(self.database).get(approved.id).status, "stale")

    def test_rule_activation_does_not_rewrite_completed_plan_history(self):
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"completed-plan")
        outcome = ScanService(self.database).run(self.profile.id)
        approved = PlanService(self.database).approve(outcome.plan_id)
        OperationService(self.database).execute(approved.id)

        rules = RuleService(self.database)
        rule_set = rules.create_set("历史规则")
        self.activate(rule_set, {"aliases": {"unused": "不会改历史"}})

        self.assertEqual(PlanService(self.database).get(approved.id).status, "completed")

    def test_rule_activation_during_execution_keeps_completed_batch_but_plan_stale(self):
        from autoanime_v3.services import operations as operations_module
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"concurrent-complete")
        rules = RuleService(self.database)
        rule_set = rules.create_set("并发成功规则")
        self.activate(rule_set, {"aliases": {"Versioned": "旧标题"}})
        outcome = ScanService(self.database).run(self.profile.id)
        approved = PlanService(self.database).approve(outcome.plan_id)
        destination = Path(approved.items[0].destination_path)
        claimed = threading.Event()
        release = threading.Event()
        result = {}
        original_execute_plan = operations_module.execute_plan

        def paused_execute_plan(*args, **kwargs):
            claimed.set()
            if not release.wait(5):
                raise TimeoutError("test did not release execution")
            return original_execute_plan(*args, **kwargs)

        def execute():
            try:
                result["batch"] = OperationService(self.database).execute(approved.id)
            except Exception as error:
                result["error"] = error

        with patch.object(operations_module, "execute_plan", side_effect=paused_execute_plan):
            worker = threading.Thread(target=execute)
            worker.start()
            self.assertTrue(claimed.wait(5))
            self.assertEqual(PlanService(self.database).get(approved.id).status, "executing")
            self.activate(rule_set, {"aliases": {"Versioned": "新标题"}})
            self.assertEqual(PlanService(self.database).get(approved.id).status, "stale")
            release.set()
            worker.join(10)

        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", result)
        self.assertEqual(result["batch"].status, "completed")
        self.assertEqual(PlanService(self.database).get(approved.id).status, "stale")
        self.assertTrue(destination.exists())

    def test_rule_activation_during_failed_execution_preserves_stale_plan(self):
        from autoanime_v3.services import operations as operations_module
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        (self.source / "测试番 S01E01.mkv").write_bytes(b"concurrent-failure")
        rules = RuleService(self.database)
        rule_set = rules.create_set("并发失败规则")
        self.activate(rule_set, {"aliases": {"Versioned": "旧标题"}})
        outcome = ScanService(self.database).run(self.profile.id)
        approved = PlanService(self.database).approve(outcome.plan_id)
        claimed = threading.Event()
        release = threading.Event()
        result = {}

        def failed_execute_plan(*args, **kwargs):
            claimed.set()
            if not release.wait(5):
                raise TimeoutError("test did not release execution")
            raise RuntimeError("controlled execution failure")

        def execute():
            try:
                OperationService(self.database).execute(approved.id)
            except Exception as error:
                result["error"] = error

        with patch.object(operations_module, "execute_plan", side_effect=failed_execute_plan):
            worker = threading.Thread(target=execute)
            worker.start()
            self.assertTrue(claimed.wait(5))
            self.activate(rule_set, {"aliases": {"Versioned": "新标题"}})
            self.assertEqual(PlanService(self.database).get(approved.id).status, "stale")
            release.set()
            worker.join(10)

        self.assertFalse(worker.is_alive())
        self.assertIsInstance(result.get("error"), RuntimeError)
        connection = sqlite3.connect(str(self.database))
        try:
            batch_status = connection.execute(
                "SELECT status FROM operation_batches ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(batch_status, "failed_rolled_back")
        self.assertEqual(PlanService(self.database).get(approved.id).status, "stale")

    def test_rollback_restores_previous_rule_version_for_new_scans(self):
        from autoanime_v3.services.rules import RuleService
        from autoanime_v3.services.scans import ScanService

        (self.source / "Rollback Alias S01E02.mkv").write_bytes(b"rollback-alias")
        rules = RuleService(self.database)
        rule_set = rules.create_set("回滚规则")
        first = self.activate(rule_set, {"aliases": {"Rollback Alias": "旧版标题"}})
        scanner = ScanService(self.database)
        scanner.run(self.profile.id)
        old_result = self.latest_identification()

        self.activate(rule_set, {"aliases": {"Rollback Alias": "新版标题"}})
        scanner.run(self.profile.id)
        new_result = self.latest_identification()

        rules.rollback(rule_set.id, first.id)
        scanner.run(self.profile.id)
        restored_result = self.latest_identification()

        self.assertEqual(old_result["title"], "旧版标题")
        self.assertEqual(new_result["title"], "新版标题")
        self.assertEqual(restored_result["title"], "旧版标题")
        self.assertEqual(restored_result["rule_version"], old_result["rule_version"])
        self.assertNotEqual(restored_result["rule_version"], new_result["rule_version"])


if __name__ == "__main__":
    unittest.main()
