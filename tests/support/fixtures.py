from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoanime.core.interfaces import RawName

_DEFAULT_ROOT = Path(__file__).parents[1] / "fixtures" / "samples"
_DIALECT_PREFIX = "dialect_"
_ROUNDTRIP_ROOT = Path(__file__).parents[1] / "fixtures" / "memory"

_VALID_LEVELS = frozenset({"high", "medium", "low"})
_VALID_SEGMENTS = frozenset({"episode", "season_pack", "movie"})
_VALID_EVIDENCE_SOURCES = frozenset({"name", "folder", "context", "none"})
_LEVEL_CONFIDENCE = {"high": 1.0, "medium": 0.6, "low": 0.2}

_MEMORY_EVIDENCE = "memory"
_KEY_LEVEL_EVIDENCE = "key_level"
_ROUNDTRIP_EVIDENCE_SOURCES = _VALID_EVIDENCE_SOURCES | {_MEMORY_EVIDENCE}
_KEY_LEVEL_VALUE_RE = re.compile(r"memory:[12]")


class FixtureError(ValueError):
    """Raised when a fixture directory does not satisfy the loader contract."""


@dataclass(frozen=True)
class FixtureFile:
    name: str


@dataclass(frozen=True)
class FixtureExpected:
    title: str
    season: int | None
    episode: int | None
    segment: str
    fansub: str | None
    level: str
    confidence: float
    missing_fields: tuple[str, ...]
    evidence: dict[str, str]


@dataclass(frozen=True)
class FixtureCase:
    id: str
    dialect: str
    folder: str | None
    parent_path: str
    files: tuple[FixtureFile, ...]
    tags: tuple[str, ...]
    notes: str | None
    expected: FixtureExpected | None = None

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
    folder = _optional_string(payload, "folder")
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
        expected=_load_expected(path) if (path / "expected.json").is_file() else None,
    )


def _load_expected(path: Path) -> FixtureExpected:
    """Load and validate an optional expected.json next to context.json."""
    expected_path = path / "expected.json"
    try:
        payload = json.loads(expected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"invalid expected.json in fixture directory: {path}") from exc

    if not isinstance(payload, dict):
        raise FixtureError(f"expected.json must contain an object in fixture directory: {path}")

    return _expected_from_payload(payload, where=f"directory: {path}")


def _expected_from_payload(
    payload: dict[str, Any], *, where: str, allow_memory_evidence: bool = False
) -> FixtureExpected:
    """Validate one expected-shaped object (ParseResult contract fields)."""
    title = _require_string(payload, "title")
    season = _optional_int(payload, "season")
    episode = _optional_int(payload, "episode")
    segment = _require_choice(payload, "segment", _VALID_SEGMENTS)
    fansub = _optional_string(payload, "fansub")
    level = _require_choice(payload, "level", _VALID_LEVELS)

    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise FixtureError(f"fixture field 'confidence' must be a number in {where}")
    if confidence != _LEVEL_CONFIDENCE[level]:
        raise FixtureError(
            f"fixture field 'confidence' must be {_LEVEL_CONFIDENCE[level]} for level "
            f"{level!r} in {where}"
        )

    raw_missing = payload.get("missing_fields", [])
    if not isinstance(raw_missing, list) or any(
        not isinstance(field, str) or not field for field in raw_missing
    ):
        raise FixtureError(
            f"fixture field 'missing_fields' must be a list of non-empty strings in {where}"
        )

    evidence = _evidence_from_payload(
        payload.get("evidence", {}),
        where=where,
        allow_memory_evidence=allow_memory_evidence,
    )

    return FixtureExpected(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub=fansub,
        level=level,
        confidence=float(confidence),
        missing_fields=tuple(raw_missing),
        evidence=evidence,
    )


def _evidence_from_payload(
    raw_evidence: object, *, where: str, allow_memory_evidence: bool
) -> dict[str, str]:
    """Validate an evidence object; roundtrip fixtures also accept memory sources."""
    if not isinstance(raw_evidence, dict):
        raise FixtureError(f"fixture field 'evidence' must be an object in {where}")
    evidence: dict[str, str] = {}
    for key, value in raw_evidence.items():
        if not isinstance(key, str) or not key:
            raise FixtureError(
                f"fixture field 'evidence' keys must be non-empty strings in {where}"
            )
        if allow_memory_evidence and key == _KEY_LEVEL_EVIDENCE:
            if not isinstance(value, str) or not _KEY_LEVEL_VALUE_RE.fullmatch(value):
                raise FixtureError(
                    f"fixture field 'evidence[{_KEY_LEVEL_EVIDENCE}]' must be "
                    f"'memory:1' or 'memory:2' in {where}"
                )
            evidence[key] = value
            continue
        valid_sources = _ROUNDTRIP_EVIDENCE_SOURCES if allow_memory_evidence else _VALID_EVIDENCE_SOURCES
        if not isinstance(value, str) or value not in valid_sources:
            raise FixtureError(
                f"fixture field 'evidence[{key}]' must be one of "
                f"{sorted(valid_sources)} in {where}"
            )
        evidence[key] = value
    return evidence


def _optional_int(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise FixtureError(f"fixture field '{key}' must be an integer or null")
    return value


def _require_choice(data: dict[str, Any], key: str, choices: frozenset[str]) -> str:
    value = data.get(key)
    if not isinstance(value, str) or value not in choices:
        raise FixtureError(
            f"fixture field '{key}' must be one of {sorted(choices)}"
        )
    return value


def _optional_string(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise FixtureError(f"fixture field '{key}' must be null or a non-empty string")
    return value


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


@dataclass(frozen=True)
class RoundtripLearn:
    """Learning side: the L1 output and the user-confirmed result."""

    parse_result: FixtureExpected
    confirmed: FixtureExpected


@dataclass(frozen=True)
class RoundtripQuery:
    """Query side: one raw release name plus the contract-level expectation."""

    name: str
    folder: str | None
    expected: FixtureExpected


@dataclass(frozen=True)
class RoundtripCase:
    """One learn-then-query memory roundtrip (PR4 L2 fixtures)."""

    id: str
    learn: RoundtripLearn
    query: RoundtripQuery


def load_roundtrip_case(path: Path) -> RoundtripCase:
    """Load one roundtrip case from a single JSON file under fixtures/memory."""
    where = f"roundtrip fixture: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"invalid {where}") from exc
    if not isinstance(payload, dict):
        raise FixtureError(f"{where} must contain an object")

    case_id = _require_string(payload, "id")
    if case_id != path.stem:
        raise FixtureError(
            f"roundtrip fixture id mismatch for {path}: expected {path.stem!r}, got {case_id!r}"
        )

    learn_payload = _require_object(payload, "learn", where)
    query_payload = _require_object(payload, "query", where)
    learn = RoundtripLearn(
        parse_result=_expected_from_payload(
            _require_object(learn_payload, "parse_result", where),
            where=f"{where} (learn.parse_result)",
        ),
        confirmed=_expected_from_payload(
            _require_object(learn_payload, "confirmed", where),
            where=f"{where} (learn.confirmed)",
        ),
    )
    query = RoundtripQuery(
        name=_require_string(query_payload, "name"),
        folder=_optional_string(query_payload, "folder"),
        expected=_expected_from_payload(
            _require_object(query_payload, "expected", where),
            where=f"{where} (query.expected)",
            allow_memory_evidence=True,
        ),
    )
    return RoundtripCase(id=case_id, learn=learn, query=query)


def load_roundtrip_cases(root: Path | None = None) -> list[RoundtripCase]:
    """Load every roundtrip case in stable id order (one JSON file per case)."""
    fixture_root = root or _ROUNDTRIP_ROOT
    if not fixture_root.is_dir():
        raise FixtureError(f"roundtrip fixture root does not exist or is not a directory: {fixture_root}")
    return [
        load_roundtrip_case(path)
        for path in sorted(fixture_root.glob("*.json"), key=lambda path: path.name)
    ]


def _require_object(data: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise FixtureError(f"roundtrip fixture field '{key}' must be an object in {where}")
    return value
