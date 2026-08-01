"""Immutable plan queries and approval preflight."""

import os
from datetime import datetime, timezone
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.repositories.jobs import JobRepository
from autoanime_v3.db.repositories.plans import PlanRepository
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import (
    ExecutionPolicyError,
    NotFoundError,
    PlanConflictError,
    StalePlanError,
)
from autoanime_v3.path_safety import validate_library_destination
from autoanime_v3.services.rules import RuleService


AUTO_APPLY_SAFE_RISK_LEVELS = frozenset({"normal"})


class PlanService:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)

    def get(self, plan_id):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            plan = PlanRepository(connection).get(plan_id)
            if plan is None:
                raise NotFoundError("Plan does not exist", {"id": plan_id})
            return plan
        finally:
            connection.close()

    def _set_status(self, plan_id, status):
        with SqliteUnitOfWork(self.database_path) as uow:
            uow.connection.execute("UPDATE plans SET status = ? WHERE id = ?", (status, plan_id))
            uow.commit()

    def _approval_context(self, plan_id):
        plan = self.get(plan_id)
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            profile = connection.execute(
                "SELECT revision, execution_policy FROM scan_profiles WHERE id = ?",
                (plan.profile_id,),
            ).fetchone()
            open_reviews = int(
                connection.execute(
                    "SELECT COUNT(*) FROM review_items WHERE scan_run_id = ? AND status = 'open'",
                    (plan.scan_run_id,),
                ).fetchone()[0]
            )
        finally:
            connection.close()
        return plan, profile, open_reviews

    def _validate_destinations(self, plan_id, connection=None):
        owns_connection = connection is None
        if owns_connection:
            connection = connect_sqlite(self.database_path)
            connection.row_factory = __import__("sqlite3").Row
        try:
            rows = connection.execute(
                """
                SELECT sr.path AS root_path, pi.destination_relative_path
                FROM plan_items pi
                JOIN storage_roots sr ON sr.id = pi.destination_root_id
                WHERE pi.plan_id = ?
                """,
                (plan_id,),
            ).fetchall()
            for row in rows:
                root = Path(row["root_path"])
                validate_library_destination(
                    root,
                    root / row["destination_relative_path"],
                )
        finally:
            if owns_connection:
                connection.close()

    def _rule_version_is_stale(self, plan, current_rule_version=None, connection=None):
        if current_rule_version is not None and current_rule_version != plan.rule_version:
            return True
        active_rule_version = RuleService(self.database_path).get_active(connection).content_hash
        return active_rule_version != plan.rule_version

    def _validate_approval(self, plan_id, current_rule_version=None, automatic=False):
        plan, profile, open_reviews = self._approval_context(plan_id)
        if profile is None:
            self._set_status(plan_id, "stale")
            raise StalePlanError("Scan profile changed after plan creation")
        if int(profile["revision"]) != plan.profile_revision:
            self._set_status(plan_id, "stale")
            raise StalePlanError("Scan profile changed after plan creation")
        execution_policy = str(profile["execution_policy"])
        if execution_policy == "dry_run":
            raise ExecutionPolicyError(
                "Dry-run plans cannot be approved or executed",
                {"plan_id": plan_id, "execution_policy": execution_policy},
            )
        if self._rule_version_is_stale(plan, current_rule_version=current_rule_version):
            self._set_status(plan_id, "stale")
            raise StalePlanError("Active rules changed after plan creation")
        allowed_statuses = {"ready"} if automatic else {"draft", "ready"}
        if plan.status not in allowed_statuses:
            raise PlanConflictError("Plan cannot be approved in its current state")
        if open_reviews or any(item.execution_status == "conflict" for item in plan.items):
            raise PlanConflictError("Open reviews or conflicts prevent plan approval")
        self._validate_destinations(plan_id)
        for item in plan.items:
            source = Path(item.source_path)
            try:
                stat = source.stat()
            except OSError:
                self._set_status(plan_id, "stale")
                raise StalePlanError("Source file is missing", {"path": str(source)})
            current_index = str(stat.st_ino) if int(stat.st_ino) else None
            if (
                int(stat.st_size) != item.source_size
                or int(stat.st_mtime_ns) != item.source_mtime_ns
                or current_index != item.source_file_index
            ):
                self._set_status(plan_id, "stale")
                raise StalePlanError("Source file identity changed", {"path": str(source)})
            if Path(item.destination_path).exists() and item.action not in {"skip"}:
                raise PlanConflictError(
                    "Destination became occupied after preview",
                    {"path": item.destination_path},
                )
        return plan

    def approve(self, plan_id, user_id=None, current_rule_version=None):
        plan, unused_job = self.approve_and_enqueue(
            plan_id,
            user_id=user_id,
            current_rule_version=current_rule_version,
        )
        return plan

    def approve_and_enqueue(
        self,
        plan_id,
        user_id=None,
        current_rule_version=None,
        automatic=False,
    ):
        idempotency_key = "execute-plan:%s" % plan_id
        with SqliteUnitOfWork(self.database_path) as uow:
            profile = uow.connection.execute(
                """
                SELECT sp.execution_policy
                FROM plans p JOIN scan_profiles sp ON sp.id = p.profile_id
                WHERE p.id = ?
                """,
                (plan_id,),
            ).fetchone()
            if profile is not None and str(profile["execution_policy"]) == "dry_run":
                raise ExecutionPolicyError(
                    "Dry-run plans cannot be approved or executed",
                    {"plan_id": plan_id, "execution_policy": "dry_run"},
                )
            repository = JobRepository(uow.connection)
            existing_job = repository.find_by_idempotency_key(idempotency_key)
            plan = PlanRepository(uow.connection).get(plan_id)
            if (
                plan is not None
                and self._rule_version_is_stale(
                    plan,
                    current_rule_version=current_rule_version,
                    connection=uow.connection,
                )
            ):
                uow.connection.execute(
                    "UPDATE plans SET status = 'stale' WHERE id = ? AND status IN ('draft', 'ready', 'approved')",
                    (plan_id,),
                )
                uow.commit()
                raise StalePlanError("Active rules changed after plan creation")
            if plan is not None and plan.status == "approved" and existing_job is not None:
                uow.commit()
                return plan, existing_job

        self._validate_approval(
            plan_id,
            current_rule_version=current_rule_version,
            automatic=automatic,
        )
        with SqliteUnitOfWork(self.database_path) as uow:
            plan = PlanRepository(uow.connection).get(plan_id)
            if plan is None:
                raise NotFoundError("Plan does not exist", {"id": plan_id})
            profile = uow.connection.execute(
                "SELECT revision, execution_policy FROM scan_profiles WHERE id = ?",
                (plan.profile_id,),
            ).fetchone()
            open_reviews = int(
                uow.connection.execute(
                    "SELECT COUNT(*) FROM review_items WHERE scan_run_id = ? AND status = 'open'",
                    (plan.scan_run_id,),
                ).fetchone()[0]
            )
            if profile is None or int(profile["revision"]) != plan.profile_revision:
                raise StalePlanError("Scan profile changed after plan creation")
            if (
                self._rule_version_is_stale(
                    plan,
                    current_rule_version=current_rule_version,
                    connection=uow.connection,
                )
            ):
                uow.connection.execute(
                    "UPDATE plans SET status = 'stale' WHERE id = ? AND status IN ('draft', 'ready', 'approved')",
                    (plan_id,),
                )
                uow.commit()
                raise StalePlanError("Active rules changed after plan creation")
            if str(profile["execution_policy"]) == "dry_run":
                raise ExecutionPolicyError(
                    "Dry-run plans cannot be approved or executed",
                    {"plan_id": plan_id, "execution_policy": "dry_run"},
                )
            repository = JobRepository(uow.connection)
            existing_job = repository.find_by_idempotency_key(idempotency_key)
            if plan.status == "approved" and existing_job is not None:
                uow.commit()
                return plan, existing_job
            allowed_statuses = {"ready"} if automatic else {"draft", "ready"}
            if plan.status not in allowed_statuses:
                raise PlanConflictError("Plan cannot be approved in its current state")
            if open_reviews or any(item.execution_status == "conflict" for item in plan.items):
                raise PlanConflictError("Open reviews or conflicts prevent plan approval")
            self._validate_destinations(plan_id, uow.connection)
            if automatic and (
                str(profile["execution_policy"]) != "auto_apply_safe"
                or not plan.items
                or not any(item.action not in {"skip", "conflict"} for item in plan.items)
                or any(
                    item.risk_level not in AUTO_APPLY_SAFE_RISK_LEVELS
                    for item in plan.items
                )
            ):
                raise PlanConflictError("Plan does not meet the automatic safety threshold")
            approved_at = datetime.now(timezone.utc).isoformat()
            updated = uow.connection.execute(
                """
                UPDATE plans SET status = 'approved', approved_by = ?, approved_at = ?
                WHERE id = ? AND status IN ('draft', 'ready')
                """,
                (user_id, approved_at, plan_id),
            ).rowcount
            if updated != 1:
                raise PlanConflictError("Plan cannot be approved in its current state")
            job = repository.find_by_idempotency_key(idempotency_key)
            if job is None:
                job = repository.enqueue(
                    "execute_plan",
                    {"plan_id": plan_id},
                    idempotency_key,
                    0,
                    approved_at,
                )
            approved = PlanRepository(uow.connection).get(plan_id)
            uow.commit()
            return approved, job

    def auto_apply_safe(self, plan_id, current_rule_version=None):
        plan, profile, open_reviews = self._approval_context(plan_id)
        if (
            profile is None
            or str(profile["execution_policy"]) != "auto_apply_safe"
            or plan.status != "ready"
            or open_reviews
            or not plan.items
            or not any(item.action not in {"skip", "conflict"} for item in plan.items)
            or any(item.execution_status == "conflict" for item in plan.items)
            or any(item.risk_level not in AUTO_APPLY_SAFE_RISK_LEVELS for item in plan.items)
        ):
            return None
        try:
            self._validate_approval(
                plan_id,
                current_rule_version=current_rule_version,
                automatic=True,
            )
        except (PlanConflictError, StalePlanError):
            return None
        try:
            return self.approve_and_enqueue(
                plan_id,
                user_id=None,
                current_rule_version=current_rule_version,
                automatic=True,
            )
        except (PlanConflictError, StalePlanError):
            return None
