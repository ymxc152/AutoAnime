"""L2 memory lookup: two-level key search, gating and hit fusion (PR4 T3).

Query-side composition layer. The decision logic here is pure (key
derivation, status/trust gating, hit drafting); the only place a database
session appears is inside ``StorageMemoryStore``, which adapts the generic
``SqliteStorage`` API (T2) to the T1 ``MemoryStore`` protocol. Tests may
substitute any ``MemoryStore`` fake.

Lookup order and gating (PR4 contract decisions):
- series level (key_level=1) first; when it misses or is filtered out, the
  exact level (key_level=2) is tried;
- rows whose status is not ACTIVE are invisible to the query side (status
  governance -- promotion, demotion, deprecation -- is the learning side's
  job, T4);
- trust < 0.5 counts as a miss; 0.5 <= trust < 0.8 supplements evidence
  without level fusion; trust >= 0.8 may fuse and raise an L1 MEDIUM result
  to HIGH. The field merge itself is T1's ``apply_memory_hit``: it only
  fills absent fields, never overwrites an existing L1 value, and stamps
  the ``memory`` / ``memory:1`` / ``memory:2`` evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from autoanime.core.enums import MemoryStatus
from autoanime.core.interfaces import MemoryStore, ParseContext, ParseResult
from autoanime.core.models import BypassList, ParseMemory
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l2.bypass import pattern_hash
from autoanime.pipeline.l2.draft import MemoryHit, apply_memory_hit
from autoanime.pipeline.l2.keys import (
    KEY_LEVEL_EXACT,
    KEY_LEVEL_SERIES,
    key_hash,
    level1_key,
    level2_key,
)
from autoanime.pipeline.l2.placeholders import backfill_title
from autoanime.pipeline.l2.trust import should_demote_to_pending, trust_score

__all__ = [
    "LookupMatch",
    "StorageMemoryStore",
    "enhance_result",
    "exact_key",
    "hit_from_memory",
    "lookup_memory",
    "series_key",
]


@dataclass(frozen=True)
class LookupMatch:
    """One consumed memory row: the drafted hit plus the row it came from."""

    hit: MemoryHit
    memory: Any


def series_key(result: ParseResult) -> str:
    """Series-level lookup key: the L1 title shape, no fansub, no numbers."""
    return level1_key(result.title)


def exact_key(result: ParseResult) -> str:
    """Exact-level lookup key: title shape + season/episode + fansub_norm."""
    return level2_key(result.title, result.season, result.episode, result.fansub)


def hit_from_memory(memory: Any, *, key_level: int) -> MemoryHit | None:
    """Draft a ``MemoryHit`` from one store row, or ``None`` when filtered.

    Filtering (query-side only): rows whose status is not ACTIVE do not
    participate, and a trust score below the 0.5 pending threshold counts as
    a miss. When the stored result carries no display title, one is
    reconstructed from the row's ``title_shape`` via placeholder backfill.
    """
    if not _is_active(getattr(memory, "status", None)):
        return None
    trust = trust_score(
        _as_count(getattr(memory, "hit_count", 0)),
        _as_count(getattr(memory, "corrected_count", 0)),
    )
    if should_demote_to_pending(trust):
        return None

    stored = getattr(memory, "result", None)
    hit = MemoryHit.from_stored_result(
        stored if isinstance(stored, Mapping) else {},
        key_level=key_level,
        trust=trust,
    )
    if hit.title is None:
        shape = getattr(memory, "title_shape", None)
        if isinstance(shape, str) and shape:
            filled = backfill_title(shape, season=hit.season, episode=hit.episode)
            if filled is not None:
                hit = replace(hit, title=filled)
    return hit


async def lookup_memory(result: ParseResult, store: MemoryStore) -> LookupMatch | None:
    """Two-level search: series level first, exact level as the fallback.

    Returns ``None`` when no level yields a participating row (missing,
    inactive, or below the pending trust threshold).
    """
    for key_level, key in (
        (KEY_LEVEL_SERIES, series_key(result)),
        (KEY_LEVEL_EXACT, exact_key(result)),
    ):
        memory = await store.find_parse_memory(key_level, key_hash(key))
        if memory is None:
            continue
        hit = hit_from_memory(memory, key_level=key_level)
        if hit is None:
            continue
        return LookupMatch(hit=hit, memory=memory)
    return None


async def enhance_result(
    result: ParseResult,
    context: ParseContext | None,
    store: MemoryStore,
) -> ParseResult | None:
    """Full query path: bypass gate -> lookup -> fusion -> hit recording.

    Returns the enhanced ParseResult on a consumed hit; ``None`` when the
    release is bypassed or memory has nothing that participates, in which
    case the orchestrator routes by the L1 result alone. ``context`` is
    accepted for protocol alignment and currently carries no query-side
    weight.
    """
    if await store.has_bypass(pattern_hash(result.title)):
        return None

    match = await lookup_memory(result, store)
    if match is None:
        return None

    enhanced = apply_memory_hit(result, match.hit)
    await store.record_hit(match.memory)
    return enhanced


class StorageMemoryStore:
    """``MemoryStore`` implementation over the generic ``SqliteStorage`` API.

    Pure composition: every session is opened inside ``SqliteStorage``
    methods; this class holds no session of its own. Reads filter in Python
    over ``list`` because the generic store exposes no field queries; the
    write path relies on ``Session.add`` save-or-update semantics for
    detached rows.
    """

    def __init__(self, storage: SqliteStorage) -> None:
        self._storage = storage

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None:
        for memory in await self._storage.list(ParseMemory):
            if memory.key_level == key_level and memory.key_hash == key_hash:
                return memory
        return None

    async def record_hit(self, parse_memory: Any) -> None:
        parse_memory.hit_count = _as_count(parse_memory.hit_count) + 1
        parse_memory.last_hit_at = datetime.now()
        await self._storage.add(parse_memory)

    async def record_correction(self, parse_memory: Any) -> None:
        parse_memory.corrected_count = _as_count(parse_memory.corrected_count) + 1
        await self._storage.add(parse_memory)

    async def has_bypass(self, pattern_hash: str) -> bool:
        entries = await self._storage.list(BypassList)
        return any(entry.pattern_hash == pattern_hash for entry in entries)


def _is_active(status: Any) -> bool:
    value = getattr(status, "value", status)
    return isinstance(value, str) and value.casefold() == MemoryStatus.ACTIVE.value


def _as_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
