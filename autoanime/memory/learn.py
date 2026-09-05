"""L2 learning/write side (PR4 T2): confirmed results -> ``parse_memory``.

This module is the composition layer of the L2 learn path: the pure pieces
(key derivation, title shapes, trust thresholds, bypass digests) come from
``autoanime.pipeline.l2`` (T1) and are reused verbatim; only the DB
write/read composition lives here, and every session stays inside the
injected ``SqliteStorage`` (this module only calls its public ``list`` /
``add`` API).

Learn flow per the unified L2 contract:

1. Bypass gate: the raw release name's ``pattern_hash`` (T1 bypass) is
   checked against the injected bypass lookup; a listed name is never
   written to memory.
2. One confirmed ``ParseResult`` is upserted at both key levels:

   - level 1 (series workhorse): ``level1_key`` -- the title shape alone;
     the stored result carries title/season/segment/fansub but **never** a
     concrete episode (per-file detail must not leak into the series entry);
   - level 2 (exact fallback): ``level2_key`` -- title shape + season /
     episode structure + normalized fansub; the stored result carries every
     confirmed field.

3. Upsert semantics on the ``uq_parse_memory_key`` (key_level, key_hash)
   constraint, implemented as find-then-insert/update:

   - new key: insert with ``hit_count = corrected_count = 0`` and
     ``status = ACTIVE`` (trust 0/0 = 1.0 per the T1 contract);
   - same key, stored result unchanged: the entry is left untouched (a
     re-confirmation is neither a hit nor a correction; hits are counted by
     the lookup side);
   - same key, stored result differs: this is a correction --
     ``corrected_count += 1``, result/source/fansub_norm/title_shape are
     replaced by the new confirmation, and ``status`` is recomputed from
     the T1 trust score (``< 0.5`` demotes to PENDING, otherwise ACTIVE).

   A correction that changes a key component (season/episode/fansub
   content) derives a *new* key, so it inserts a new exact-level entry
   while the series-level entry (whose key only depends on the title) is
   updated in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from autoanime.core.enums import MemorySource, MemoryStatus
from autoanime.core.interfaces import ParseResult, Storage
from autoanime.core.models import BypassList, ParseMemory
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
    """``MemoryWriteStore`` + ``BypassLookup`` over the generic ``Storage`` API.

    Keeps every DB session inside the injected ``SqliteStorage``: lookups use
    its public ``list`` and filter by key in Python (the v2 corpus is small;
    T4 may add an indexed store implementation without touching this module).
    """

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None:
        for entry in await self._storage.list(ParseMemory):
            if entry.key_level == key_level and entry.key_hash == key_hash:
                return entry
        return None

    async def add(self, parse_memory: Any) -> None:
        await self._storage.add(parse_memory)

    async def has_bypass(self, pattern_hash: str) -> bool:
        return any(
            row.pattern_hash == pattern_hash for row in await self._storage.list(BypassList)
        )


def stored_result_for(confirmed: ParseResult, *, key_level: int) -> dict[str, object]:
    """JSON payload stored in ``parse_memory.result`` for one confirmed result.

    Level 1 deliberately stores ``episode: None``: the series-level entry
    must never pin a concrete episode. Values are the JSON-safe shapes that
    ``MemoryHit.from_stored_result`` (T3 lookup side) reads back.
    """
    if key_level == KEY_LEVEL_SERIES:
        return {
            "title": confirmed.title,
            "season": confirmed.season,
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

    if dict(existing.result or {}) != result:
        # Same key, different confirmed content: a correction.
        existing.corrected_count += 1
        existing.result = result
        existing.source = source
        existing.fansub_norm = fansub_norm(confirmed.fansub)
        existing.title_shape = build_title_shape(confirmed.title)
        existing.status = status_for_counts(existing.hit_count, existing.corrected_count)
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
