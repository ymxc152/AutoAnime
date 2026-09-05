from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from autoanime.core.enums import MemorySource, MemoryStatus
from autoanime.core.interfaces import ParseResult
from autoanime.core.models import ParseMemory
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l2 import (
    KEY_LEVEL_EXACT,
    KEY_LEVEL_SERIES,
    build_title_shape,
    fansub_norm,
    key_hash,
    level1_key,
    level2_key,
    pattern_hash,
    should_demote_to_pending,
    trust_score,
)

__all__ = [
    "BypassLookup",
    "LearnOutcome",
    "MemoryWriteStore",
    "StorageMemoryAccess",
    "derive_memory_key",
    "learn_confirmation",
    "status_for_counts",
    "stored_result_for",
    "upsert_parse_memory",
]


@runtime_checkable
class MemoryWriteStore(Protocol):
    """Write-side persistence surface: lookup by (key_level, key_hash) + add."""

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None: ...
    async def add(self, parse_memory: Any) -> None: ...


@runtime_checkable
class BypassLookup(Protocol):
    """Bypass read interface (PR4 contract).

    The DB-backed implementation lands with the T4 recognizer segment;
    tests inject fakes, and :class:`StorageMemoryAccess` offers a generic
    storage-backed one for the CLI composition.
    """

    async def has_bypass(self, pattern_hash: str) -> bool: ...


@dataclass(frozen=True)
class LearnOutcome:
    """Result of one learning call: the upserted rows (empty when bypassed)."""

    entries: tuple[Any, ...]
    bypassed: bool


class StorageMemoryAccess:
    """Write-side + bypass access over ``SqliteStorage`` DB predicates."""

    def __init__(self, storage: SqliteStorage) -> None:
        self._storage = storage

    async def find_parse_memory(self, key_level: int, key_hash: str) -> ParseMemory | None:
        return await self._storage.find_parse_memory(key_level, key_hash)

    async def add(self, parse_memory: ParseMemory) -> None:
        await self._storage.add(parse_memory)

    async def has_bypass(self, pattern_hash: str) -> bool:
        return await self._storage.find_bypass(pattern_hash) is not None



def stored_result_for(confirmed: ParseResult, *, key_level: int) -> dict[str, object]:
    """JSON payload stored in ``parse_memory.result`` for one confirmed result.

    Level 1 deliberately stores ``episode: None`` and a ``seasons`` list: the
    series-level entry must never pin a concrete episode, and one title shape
    may legitimately cover several seasons (the season is merged in, never
    corrected). Values are the JSON-safe shapes that
    ``MemoryHit.from_stored_result`` (T3 lookup side) reads back.
    """
    if key_level == KEY_LEVEL_SERIES:
        return {
            "title": confirmed.title,
            "seasons": [confirmed.season] if confirmed.season is not None else [],
            "episode": None,
            "segment": confirmed.segment.value,
            "fansub": confirmed.fansub,
        }
    if key_level == KEY_LEVEL_EXACT:
        return {
            "title": confirmed.title,
            "season": confirmed.season,
            "episode": confirmed.episode,
            "segment": confirmed.segment.value,
            "fansub": confirmed.fansub,
        }
    raise ValueError(f"unknown key level: {key_level}")


def derive_memory_key(confirmed: ParseResult, *, key_level: int) -> str:
    """Canonical key text for one confirmed result at the given level (T1 keys)."""
    if key_level == KEY_LEVEL_SERIES:
        return level1_key(confirmed.title)
    if key_level == KEY_LEVEL_EXACT:
        return level2_key(confirmed.title, confirmed.season, confirmed.episode, confirmed.fansub)
    raise ValueError(f"unknown key level: {key_level}")


def status_for_counts(hit_count: int, corrected_count: int) -> MemoryStatus:
    """ACTIVE unless the T1 trust score dropped below the 0.5 pending threshold."""
    if should_demote_to_pending(trust_score(hit_count, corrected_count)):
        return MemoryStatus.PENDING
    return MemoryStatus.ACTIVE


def _conflicting_result(
    existing: dict[str, object], incoming: dict[str, object], *, key_level: int
) -> bool:
    """Whether two same-key answers disagree on a semantic memory field.

    Series level never conflicts: the title shape is fansub- and season-
    agnostic by contract, and a differing season is a legal multi-season
    observation that ``_merge_series_result`` unions. At exact level the key
    already pins season/episode/fansub, so a disagreement means the
    non-key ``segment`` answer contradicts: a real correction.
    """
    if key_level == KEY_LEVEL_SERIES:
        return False
    fields: tuple[str, ...] = ("season", "episode", "segment")
    for field_name in fields:
        old = existing.get(field_name)
        new = incoming.get(field_name)
        if old is not None and new is not None and old != new:
            return True
    return False


def _as_season_list(value: object) -> list[int]:
    """Season ints from a stored ``seasons`` payload; anything else is empty."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, int) and not isinstance(v, bool)]
    return []


def _as_season_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _merge_series_result(
    existing: dict[str, object], incoming: dict[str, object]
) -> dict[str, object]:
    """Union seasons and fill absent fields without overwriting confirmed values.

    Legacy rows that carry a single ``season`` key are migrated to the
    ``seasons`` list shape on merge.
    """
    merged = dict(existing)
    for field_name in ("title", "episode", "segment", "fansub"):
        if merged.get(field_name) is None and incoming.get(field_name) is not None:
            merged[field_name] = incoming[field_name]
    seasons = set(_as_season_list(merged.pop("seasons", None)))
    legacy_season = _as_season_int(merged.pop("season", None))
    if legacy_season is not None:
        seasons.add(legacy_season)
    incoming_seasons = _as_season_list(incoming.get("seasons"))
    if not incoming_seasons:
        single = _as_season_int(incoming.get("season"))
        if single is not None:
            incoming_seasons = [single]
    seasons.update(incoming_seasons)
    merged["seasons"] = sorted(seasons)
    return merged


def _merge_non_conflicting_result(
    existing: dict[str, object], incoming: dict[str, object]
) -> dict[str, object]:
    """Fill absent stored fields without overwriting an already confirmed value."""
    merged = dict(existing)
    for field_name, value in incoming.items():
        if merged.get(field_name) is None and value is not None:
            merged[field_name] = value
    return merged


async def upsert_parse_memory(
    store: MemoryWriteStore,
    *,
    confirmed: ParseResult,
    key_level: int,
    source: MemorySource,
) -> Any:
    """Insert or update one ``parse_memory`` row for the confirmed result.

    See the module docstring for the exact conflict semantics.
    """
    digest = key_hash(derive_memory_key(confirmed, key_level=key_level))
    result = stored_result_for(confirmed, key_level=key_level)

    existing = await store.find_parse_memory(key_level, digest)
    if existing is None:
        entry = ParseMemory(
            key_level=key_level,
            key_hash=digest,
            fansub_norm=fansub_norm(confirmed.fansub),
            title_shape=build_title_shape(confirmed.title),
            result=result,
            source=source,
            hit_count=0,
            corrected_count=0,
            status=status_for_counts(0, 0),
        )
        await store.add(entry)
        return entry

    old_result = dict(existing.result or {})
    if old_result != result:
        if key_level == KEY_LEVEL_SERIES:
            # A differing season under the same series key is a legal
            # multi-season observation: union it, never treat it as a
            # correction (the series key is season-agnostic by contract).
            existing.result = _merge_series_result(old_result, result)
        elif _conflicting_result(old_result, result, key_level=key_level):
            # Same exact key, different confirmed content: a correction.
            existing.corrected_count += 1
            existing.result = result
            existing.source = source
            existing.fansub_norm = fansub_norm(confirmed.fansub)
            existing.title_shape = build_title_shape(confirmed.title)
            if existing.status is not MemoryStatus.DEPRECATED:
                # DEPRECATED is terminal (T4 governance); corrections keep it.
                existing.status = status_for_counts(
                    existing.hit_count, existing.corrected_count
                )
        else:
            # A completeness fill: update without treating a different-shaped
            # observation as a user correction.
            existing.result = _merge_non_conflicting_result(old_result, result)
        await store.add(existing)
    # Same key, same content: re-confirmation is a no-op (no hit counting here).
    return existing


async def learn_confirmation(
    store: MemoryWriteStore,
    *,
    confirmed: ParseResult,
    raw_name: str,
    source: MemorySource = MemorySource.MANUAL,
    bypass_lookup: BypassLookup | None = None,
) -> LearnOutcome:
    """Learn one confirmed result: bypass gate, then upsert both key levels.

    ``raw_name`` is the release name the confirmation refers to; its T1
    bypass digest is checked first, and a listed name writes nothing.
    """
    if bypass_lookup is not None and await bypass_lookup.has_bypass(pattern_hash(raw_name)):
        return LearnOutcome(entries=(), bypassed=True)

    entries: list[Any] = []
    for key_level in (KEY_LEVEL_SERIES, KEY_LEVEL_EXACT):
        entries.append(
            await upsert_parse_memory(
                store, confirmed=confirmed, key_level=key_level, source=source
            )
        )
    return LearnOutcome(entries=tuple(entries), bypassed=False)
