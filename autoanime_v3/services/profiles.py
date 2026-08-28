"""Scan-profile creation, optimistic updates, and logical deletion."""

import json
from datetime import datetime, timezone
from pathlib import Path

from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.profile_snapshots import build_profile_snapshot, encode_profile_snapshot
from autoanime_v3.db.repositories.profiles import ProfileRepository
from autoanime_v3.db.repositories.roots import RootRepository
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import (
    NotFoundError,
    RevisionConflictError,
    UnsafeRootError,
    ValidationError,
)
from autoanime_v3.domain.enums import ExecutionPolicy, OperationMode, RootKind
from autoanime_v3.services.roots import path_is_within


class ProfileService:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)

    def create_profile(self, command):
        if command.mode not in {item.value for item in OperationMode}:
            raise ValidationError("Unsupported operation mode", {"mode": command.mode})
        if command.execution_policy not in {item.value for item in ExecutionPolicy}:
            raise ValidationError(
                "Unsupported execution policy", {"policy": command.execution_policy}
            )
        if not 0 <= int(command.min_confidence) <= 100:
            raise ValidationError("Minimum confidence must be between 0 and 100")
        if int(command.stability_seconds) < 0:
            raise ValidationError("Stability seconds cannot be negative")
        with SqliteUnitOfWork(self.database_path) as uow:
            roots = RootRepository(uow.connection)
            source = roots.get(command.source_root_id)
            library = roots.get(command.library_root_id)
            if source is None or source.kind != RootKind.SOURCE.value:
                raise ValidationError("Profile source must reference a source root")
            if library is None or library.kind != RootKind.LIBRARY.value:
                raise ValidationError("Profile library must reference a library root")
            profile = ProfileRepository(uow.connection).create(command)
            uow.commit()
            return profile

    def delete_profile(self, profile_id, revision):
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = ProfileRepository(uow.connection)
            existing_row = uow.connection.execute(
                "SELECT * FROM scan_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if existing_row is None or existing_row["deleted_at"] is not None:
                raise NotFoundError("Scan profile does not exist", {"id": profile_id})
            if int(existing_row["revision"]) != int(revision):
                raise RevisionConflictError(
                    "Scan profile was changed by another request",
                    {"expected_revision": revision, "actual_revision": existing_row["revision"]},
                )
            active_jobs = uow.connection.execute(
                "SELECT payload_json FROM jobs WHERE job_type = 'scan' AND status IN ('queued', 'running', 'leased')"
            ).fetchall()
            for job in active_jobs:
                try:
                    payload = json.loads(job["payload_json"] or "{}")
                except (TypeError, ValueError):
                    continue
                if int(payload.get("profile_id", -1)) == int(profile_id):
                    raise ValidationError(
                        "Scan profile has an active scan job and cannot be deleted; wait or cancel it first",
                        {"profile_id": profile_id},
                    )
            snapshot = build_profile_snapshot(
                uow.connection,
                profile_id,
                profile_row=existing_row,
                snapshot_at=datetime.now(timezone.utc).isoformat(),
            )
            deleted = uow.connection.execute(
                """
                UPDATE scan_profiles
                SET enabled = 0, watch_enabled = 0, deleted_at = CURRENT_TIMESTAMP,
                    deleted_snapshot_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND revision = ? AND deleted_at IS NULL
                """,
                (encode_profile_snapshot(snapshot), profile_id, revision),
            ).rowcount
            if deleted != 1:
                raise ValidationError(
                    "Scan profile was changed by another request",
                    {"expected_revision": revision, "actual_revision": existing_row["revision"]},
                )
            uow.connection.execute("DELETE FROM schedules WHERE profile_id = ?", (profile_id,))
            uow.connection.execute("DELETE FROM webhook_sources WHERE profile_id = ?", (profile_id,))
            uow.commit()
            return {"id": profile_id, "deleted": True}

    def update_profile(self, profile_id, revision, patch):
        allowed = {
            "name",
            "source_root_id",
            "library_root_id",
            "mode",
            "execution_policy",
            "min_confidence",
            "stability_seconds",
            "watch_enabled",
            "enabled",
        }
        unsupported = set(patch) - allowed
        if unsupported:
            raise ValidationError("Unsupported profile fields", {"fields": sorted(unsupported)})
        if not patch:
            raise ValidationError("Profile update is empty")
        if "name" in patch and not str(patch["name"]).strip():
            raise ValidationError("Profile name cannot be empty")
        if "mode" in patch and patch["mode"] not in {item.value for item in OperationMode}:
            raise ValidationError("Unsupported operation mode", {"mode": patch["mode"]})
        if "execution_policy" in patch and patch["execution_policy"] not in {
            item.value for item in ExecutionPolicy
        }:
            raise ValidationError(
                "Unsupported execution policy", {"policy": patch["execution_policy"]}
            )
        try:
            if "min_confidence" in patch and not 0 <= int(patch["min_confidence"]) <= 100:
                raise ValidationError("Minimum confidence must be between 0 and 100")
            if "stability_seconds" in patch and int(patch["stability_seconds"]) < 0:
                raise ValidationError("Stability seconds cannot be negative")
        except (TypeError, ValueError):
            raise ValidationError("Profile numeric fields must contain integers")
        for field in {"watch_enabled", "enabled"} & set(patch):
            if type(patch[field]) is not bool:
                raise ValidationError("Profile boolean fields must be true or false", {"field": field})
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = ProfileRepository(uow.connection)
            existing = repository.get(profile_id)
            if existing is None:
                raise NotFoundError("Scan profile does not exist", {"id": profile_id})
            deleted_at = uow.connection.execute(
                "SELECT deleted_at FROM scan_profiles WHERE id = ?", (profile_id,)
            ).fetchone()["deleted_at"]
            if deleted_at is not None:
                raise ValidationError("Scan profile has been deleted and cannot be changed", {"profile_id": profile_id})
            roots = RootRepository(uow.connection)
            source = roots.get(patch.get("source_root_id", existing.source_root_id))
            library = roots.get(patch.get("library_root_id", existing.library_root_id))
            if source is None or source.kind != RootKind.SOURCE.value:
                raise ValidationError("Profile source must reference a source root")
            if library is None or library.kind != RootKind.LIBRARY.value:
                raise ValidationError("Profile library must reference a library root")
            if path_is_within(library.path, source.path) or path_is_within(
                source.path, library.path
            ):
                raise UnsafeRootError(
                    "Source and library roots cannot be equal or nested",
                    {"source": source.path, "library": library.path},
                )
            profile, updated = repository.update(profile_id, revision, patch)
            if not updated:
                raise RevisionConflictError(
                    "Scan profile was changed by another request",
                    {"expected_revision": revision, "actual_revision": existing.revision},
                )
            uow.commit()
            return profile
