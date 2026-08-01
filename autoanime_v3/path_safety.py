"""Physical destination containment checks for file-changing operations."""

import os
import stat
from pathlib import Path

from autoanime_v3.domain.errors import PlanConflictError


WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_symlink_or_reparse_point(path):
    try:
        metadata = os.lstat(str(path))
    except OSError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        int(getattr(metadata, "st_file_attributes", 0)) & WINDOWS_REPARSE_POINT
    )


def validate_library_destination(root_path, destination_path):
    root = Path(root_path)
    destination = Path(destination_path)
    try:
        relative = destination.relative_to(root)
    except ValueError:
        raise PlanConflictError(
            "Destination escapes its registered library root",
            {"root": str(root), "destination": str(destination)},
        )
    if relative.is_absolute() or ".." in relative.parts:
        raise PlanConflictError(
            "Destination escapes its registered library root",
            {"root": str(root), "destination": str(destination)},
        )

    current = root
    for part in (None,) + relative.parts:
        if part is not None:
            current = current / part
        if os.path.lexists(str(current)) and _is_symlink_or_reparse_point(current):
            raise PlanConflictError(
                "Destination path contains a symlink or reparse point",
                {"path": str(current)},
            )

    try:
        physical_root = root.resolve(strict=False)
        physical_destination = destination.resolve(strict=False)
        physical_destination.relative_to(physical_root)
    except (OSError, RuntimeError, ValueError):
        raise PlanConflictError(
            "Destination resolves outside its registered library root",
            {"root": str(root), "destination": str(destination)},
        )
