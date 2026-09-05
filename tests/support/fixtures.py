from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoanime.core.interfaces import RawName

_DEFAULT_ROOT = Path(__file__).parents[1] / "fixtures" / "samples"
_DIALECT_PREFIX = "dialect_"


class FixtureError(ValueError):
    """Raised when a fixture directory does not satisfy the loader contract."""


@dataclass(frozen=True)
class FixtureFile:
    name: str


@dataclass(frozen=True)
class FixtureCase:
    id: str
    dialect: str
    folder: str
    parent_path: str
    files: tuple[FixtureFile, ...]
    tags: tuple[str, ...]
    notes: str | None

    def to_raw_names(self) -> list[RawName]:
        return [
            RawName(name=file.name, folder=self.folder, parent_path=self.parent_path)
            for file in self.files
        ]


def _require_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise FixtureError(f"fixture field '{key}' must be a non-empty string")
    return value


def load_case(path: Path) -> FixtureCase:
    """Load one case directory containing a context.json file."""
    context_path = path / "context.json"
    if not context_path.is_file():
        raise FixtureError(f"missing context.json in fixture directory: {path}")

    try:
        payload = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"invalid context.json in fixture directory: {path}") from exc

    if not isinstance(payload, dict):
        raise FixtureError(f"context.json must contain an object in fixture directory: {path}")

    case_id = _require_string(payload, "id")
    dialect = _require_string(payload, "dialect")
    folder = _require_string(payload, "folder")
    parent_path = _require_string(payload, "parent_path")

    expected_dialect = path.parent.name.removeprefix(_DIALECT_PREFIX)
    if dialect.casefold() != expected_dialect.casefold():
        raise FixtureError(
            f"fixture dialect mismatch for {path}: expected {expected_dialect!r}, got {dialect!r}"
        )

    if case_id != path.name:
        raise FixtureError(
            f"fixture id mismatch for {path}: expected {path.name!r}, got {case_id!r}"
        )

    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise FixtureError(f"fixture field 'files' must be a non-empty list in directory: {path}")

    files: list[FixtureFile] = []
    for index, raw_file in enumerate(raw_files):
        if not isinstance(raw_file, dict):
            raise FixtureError(f"fixture files[{index}] must be an object in directory: {path}")
        name = raw_file.get("name")
        if not isinstance(name, str) or not name:
            raise FixtureError(
                f"fixture files[{index}].name must be a non-empty string in directory: {path}"
            )
        files.append(FixtureFile(name=name))

    raw_tags = payload.get("tags", [])
    if not isinstance(raw_tags, list) or any(not isinstance(tag, str) for tag in raw_tags):
        raise FixtureError(f"fixture field 'tags' must be a list of strings in directory: {path}")

    notes = payload.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise FixtureError(f"fixture field 'notes' must be a string in directory: {path}")

    return FixtureCase(
        id=case_id,
        dialect=dialect,
        folder=folder,
        parent_path=parent_path,
        files=tuple(files),
        tags=tuple(raw_tags),
        notes=notes,
    )


def _dialect_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FixtureError(f"fixture root does not exist or is not a directory: {root}")
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name.startswith(_DIALECT_PREFIX)),
        key=lambda path: path.name,
    )


def load_dialects(*dialects: str, root: Path | None = None) -> list[FixtureCase]:
    """Load cases from the selected dialects; no arguments loads every dialect."""
    fixture_root = root or _DEFAULT_ROOT
    dialect_dirs = _dialect_directories(fixture_root)

    requested = {
        dialect.strip().removeprefix(_DIALECT_PREFIX).casefold()
        for dialect in dialects
        if dialect.strip()
    }
    if not requested:
        selected_dirs = dialect_dirs
    else:
        available = {path.name.removeprefix(_DIALECT_PREFIX).casefold() for path in dialect_dirs}
        unknown = sorted(requested - available)
        if unknown:
            raise FixtureError(f"unknown fixture dialect(s): {', '.join(unknown)}")
        selected_dirs = [
            path
            for path in dialect_dirs
            if path.name.removeprefix(_DIALECT_PREFIX).casefold() in requested
        ]

    cases: list[FixtureCase] = []
    for dialect_dir in selected_dirs:
        for case_dir in sorted((path for path in dialect_dir.iterdir() if path.is_dir()), key=lambda path: path.name):
            cases.append(load_case(case_dir))
    return cases


def load_all(root: Path | None = None) -> list[FixtureCase]:
    """Load every dialect fixture in stable dialect/case order."""
    return load_dialects(root=root or _DEFAULT_ROOT)
