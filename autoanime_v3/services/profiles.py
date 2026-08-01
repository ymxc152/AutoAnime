"""Scan-profile creation and optimistic updates."""

from pathlib import Path

from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.repositories.profiles import ProfileRepository
from autoanime_v3.db.repositories.roots import RootRepository
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import NotFoundError, RevisionConflictError, ValidationError
from autoanime_v3.domain.enums import ExecutionPolicy, OperationMode, RootKind


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

    def update_profile(self, profile_id, revision, patch):
        allowed = {
            "name",
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
            profile, updated = repository.update(profile_id, revision, patch)
            if not updated:
                raise RevisionConflictError(
                    "Scan profile was changed by another request",
                    {"expected_revision": revision, "actual_revision": existing.revision},
                )
            uow.commit()
            return profile
