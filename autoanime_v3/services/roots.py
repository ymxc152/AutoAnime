"""Storage-root validation and safe target resolution."""

import os
from datetime import datetime, timezone
from pathlib import Path

from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.repositories.roots import RootRepository
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.entities import RootHealth
from autoanime_v3.domain.errors import (
    DuplicateRootError,
    NotFoundError,
    PathOutsideRootError,
    UnsafeRootError,
    ValidationError,
)
from autoanime_v3.domain.enums import RootKind


def normalize_windows_path(path):
    resolved = Path(path).expanduser().resolve(strict=False)
    return os.path.normpath(str(resolved)).casefold()


def path_is_within(path, parent):
    normalized_path = normalize_windows_path(path)
    normalized_parent = normalize_windows_path(parent)
    try:
        return os.path.commonpath([normalized_path, normalized_parent]) == normalized_parent
    except ValueError:
        return False


class RootService:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)

    def create_root(self, kind, path):
        valid_kinds = {item.value for item in RootKind}
        if kind not in valid_kinds:
            raise ValidationError("Unsupported root kind", {"kind": kind})
        display_path = str(Path(path).expanduser().resolve(strict=False))
        normalized = normalize_windows_path(path)
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = RootRepository(uow.connection)
            duplicate = repository.find_by_normalized_path(normalized)
            if duplicate is not None and {duplicate.kind, kind} == {
                RootKind.SOURCE.value,
                RootKind.LIBRARY.value,
            }:
                raise UnsafeRootError(
                    "Source and library roots cannot use the same path",
                    {"path": display_path},
                )
            if duplicate is not None:
                raise DuplicateRootError("Storage root already exists", {"path": display_path})
            roots = repository.list_enabled()
            if kind == RootKind.LIBRARY.value:
                for root in roots:
                    if root.kind == RootKind.SOURCE.value and path_is_within(display_path, root.path):
                        raise UnsafeRootError(
                            "Library root cannot equal or be below a source root",
                            {"source": root.path, "library": display_path},
                        )
            if kind == RootKind.SOURCE.value:
                for root in roots:
                    if root.kind == RootKind.LIBRARY.value and path_is_within(root.path, display_path):
                        raise UnsafeRootError(
                            "Existing library root cannot be below the new source root",
                            {"source": display_path, "library": root.path},
                        )
            created = repository.create(kind, display_path, normalized)
            uow.commit()
            return created

    def get_root(self, root_id):
        with SqliteUnitOfWork(self.database_path) as uow:
            root = RootRepository(uow.connection).get(root_id)
        if root is None:
            raise NotFoundError("Storage root does not exist", {"id": root_id})
        return root

    def update_root(self, root_id, patch):
        unsupported = set(patch) - {"enabled"}
        if unsupported:
            raise ValidationError(
                "Only the enabled state can be changed for an existing root; add a new root to change paths",
                {"unsupported": sorted(unsupported)},
            )
        if "enabled" not in patch:
            raise ValidationError("Root update is empty")
        if type(patch["enabled"]) is not bool:
            raise ValidationError("Root enabled state must be true or false")
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = RootRepository(uow.connection)
            current = repository.get(root_id)
            if current is None:
                raise NotFoundError("Storage root does not exist", {"id": root_id})
            if patch["enabled"] and not current.enabled:
                for other in repository.list_enabled():
                    if current.kind == RootKind.SOURCE.value and other.kind == RootKind.LIBRARY.value:
                        if path_is_within(other.path, current.path):
                            raise UnsafeRootError(
                                "Library root cannot equal or be below a source root",
                                {"source": current.path, "library": other.path},
                            )
                    if current.kind == RootKind.LIBRARY.value and other.kind == RootKind.SOURCE.value:
                        if path_is_within(current.path, other.path):
                            raise UnsafeRootError(
                                "Library root cannot equal or be below a source root",
                                {"source": other.path, "library": current.path},
                            )
            uow.connection.execute(
                "UPDATE storage_roots SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(bool(patch["enabled"])), root_id),
            )
            result = repository.get(root_id)
            uow.commit()
            return result

    def resolve_target(self, root_id, relative_path):
        root = self.get_root(root_id)
        relative = Path(relative_path)
        if relative.is_absolute():
            raise PathOutsideRootError("Operation target must be relative to its root")
        candidate = (Path(root.path) / relative).resolve(strict=False)
        if not path_is_within(candidate, root.path):
            raise PathOutsideRootError(
                "Operation target escapes its registered root",
                {"root": root.path, "target": str(candidate)},
            )
        return candidate

    def validate_root(self, root_id):
        root = self.get_root(root_id)
        path = Path(root.path)
        exists = path.is_dir()
        readable = exists and os.access(str(path), os.R_OK)
        writable = exists and os.access(str(path), os.W_OK)
        status = "healthy" if readable and writable else "unavailable"
        checked_at = datetime.now(timezone.utc).isoformat()
        with SqliteUnitOfWork(self.database_path) as uow:
            RootRepository(uow.connection).update_health(root_id, status, checked_at)
            uow.commit()
        return RootHealth(root_id, exists, readable, writable, status)
