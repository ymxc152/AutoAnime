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

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from autoanime.core.enums import MemoryStatus
from autoanime.core.interfaces import MemoryStore, ParseContext, ParseResult
from autoanime.core.models import ParseMemory
from autoanime.memory.governance import MemoryGovernance
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
    *,
    raw_name: str | None = None,
    operation_id: str | None = None,
) -> ParseResult | None:
    """Full query path: optional raw-name bypass gate -> lookup -> fusion -> hit recording.

    Returns the enhanced ParseResult on a consumed hit; ``None`` when the
    release is bypassed or memory has nothing that participates, in which
    case the orchestrator routes by the L1 result alone. ``raw_name`` may be
    supplied by non-orchestrator callers; the orchestrator owns the
    authoritative raw-name gate and may leave it out. ``operation_id`` groups
    the hit audit rows of one parse pass into one batch.
    """
    if raw_name is not None and await store.has_bypass(pattern_hash(raw_name)):
        return None
    match = await lookup_memory(result, store)
    if match is None:
        return None

    enhanced = apply_memory_hit(result, match.hit)
    await store.record_hit(match.memory, operation_id=operation_id)
    return enhanced


class StorageMemoryStore:
    """``MemoryStore`` implementation over ``SqliteStorage``.

    Key and bypass reads use SQLite predicates. Hit counting and audit writes
    share one transaction so the counter and its audit row cannot diverge.
    """

    def __init__(
        self, storage: SqliteStorage, *, audit_governance: MemoryGovernance | None = None
    ) -> None:
        self._storage = storage
        self._audit_governance = audit_governance

    async def find_parse_memory(self, key_level: int, key_hash: str) -> ParseMemory | None:
        return await self._storage.find_parse_memory(key_level, key_hash)

    async def find_alias_key(self, title_shape_norm: str) -> str | None:
        # PR7 M2b: 透传 title_aliases 读侧，否则 orchestrator 的 alias 环
        # 鸭子类型探测失败、生产装配下静默退化为参考链路径。
        return await self._storage.find_alias_key(title_shape_norm)

    async def find_alias_row(
        self, title_shape_norm: str
    ) -> tuple[str, str | None] | None:
        # A1'（拍板）：带 source 的 alias 读侧透传——manual 行触发确认名
        # 覆盖；缺席时 orchestrator 退回 find_alias_key（不覆盖）。
        row_finder = getattr(self._storage, "find_alias_row", None)
        if not callable(row_finder):
            return None
        row_lookup = cast(
            "Callable[[str], Awaitable[tuple[str, str | None] | None]]", row_finder
        )
        return await row_lookup(title_shape_norm)

    async def record_hit(
        self, parse_memory: Any, *, operation_id: str | None = None
    ) -> None:
        new_count = _as_count(parse_memory.hit_count) + 1
        hit_at = datetime.now()
        async with self._storage.transaction() as session:
            row = await session.merge(parse_memory)
            row.hit_count = new_count
            row.last_hit_at = hit_at
            if self._audit_governance is not None and row.id is not None:
                session.add(
                    self._audit_governance.memory_hit_audit_row(
                        operation_id=operation_id or uuid4().hex,
                        entity_id=row.id,
                        instruction={
                            "key_level": row.key_level,
                            "trust": trust_score(
                                new_count,
                                _as_count(row.corrected_count),
                            ),
                        },
                    )
                )
        parse_memory.hit_count = new_count
        parse_memory.last_hit_at = hit_at

    async def record_correction(self, parse_memory: Any) -> None:
        new_count = _as_count(parse_memory.corrected_count) + 1
        async with self._storage.transaction() as session:
            row = await session.merge(parse_memory)
            row.corrected_count = new_count
        parse_memory.corrected_count = new_count

    async def has_bypass(self, pattern_hash: str) -> bool:
        return await self._storage.find_bypass(pattern_hash) is not None



def _is_active(status: Any) -> bool:
    value = getattr(status, "value", status)
    return isinstance(value, str) and value.casefold() == MemoryStatus.ACTIVE.value


def _as_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
