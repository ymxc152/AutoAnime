"""L2 governance: bypass list, trust-driven status demotion, and audit log.

Composes on top of the generic ``SqliteStorage`` API (add/get/list/delete);
every DB session stays inside this module and ``store.py`` is not touched.

Demotion policy (PR4 T4; thresholds come from the T1 trust module):

- ``trust < TRUST_PENDING_THRESHOLD`` (0.5): an ACTIVE entry is demoted to
  PENDING. This rule always applies first, so the pinned contract
  "trust < 0.5 -> PENDING" holds on every sweep.
- An entry already PENDING that still fails the threshold is deprecated when
  it shows no recent hit: ``last_hit_at is None`` (never hit once, i.e. the
  corrected-on-every-observation case) or the last hit is at least
  ``no_hit_days_for_deprecation`` days old (default 30).
- DEPRECATED is terminal: a sweep never revives or promotes an entry, and no
  entry is auto-promoted from PENDING back to ACTIVE (recovery is a
  later-PR concern).

Every audit row carries the ``operation_id`` batch field: one sweep run (or
one caller-supplied batch id) groups its hit/demotion events together. This
PR only records L2 hit/demotion events; wiring hit events into the lookup
path is T5's job via :meth:`MemoryGovernance.record_memory_hit_audit`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from autoanime.core.enums import Actor, MemoryStatus
from autoanime.core.models import AuditLog, BypassList, ParseMemory
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l2 import (
    is_bypassed,
    pattern_hash,
    should_demote_to_pending,
    trust_score,
)

ENTITY_PARSE_MEMORY = "parse_memory"
ENTITY_BYPASS_LIST = "bypass_list"

ACTION_MEMORY_HIT = "memory_hit"
ACTION_DEMOTE_PENDING = "demote_pending"
ACTION_DEPRECATE = "deprecate"
ACTION_BYPASS_ADD = "bypass_add"

DEFAULT_NO_HIT_DAYS_FOR_DEPRECATION = 30


def memory_hit_audit_row(
    *,
    operation_id: str,
    entity_id: int,
    instruction: dict[str, object] | None = None,
) -> AuditLog:
    """Build a memory-hit audit row for use inside a caller transaction."""
    return AuditLog(
        operation_id=operation_id,
        entity=ENTITY_PARSE_MEMORY,
        action=ACTION_MEMORY_HIT,
        entity_id=entity_id,
        instruction=instruction or {},
        actor=Actor.AUTO,
    )


@dataclass(frozen=True)
class StatusDecision:
    """Outcome of the pure status state machine for one ParseMemory row."""

    status: MemoryStatus
    action: str | None


@dataclass(frozen=True)
class StatusSweepReport:
    """Summary of one batch status sweep over every ParseMemory row."""

    operation_id: str
    demoted_to_pending: int
    deprecated: int
    unchanged: int


def status_decision(
    *,
    current: MemoryStatus,
    hit_count: int,
    corrected_count: int,
    last_hit_at: datetime | None,
    now: datetime,
    no_hit_days_for_deprecation: int = DEFAULT_NO_HIT_DAYS_FOR_DEPRECATION,
) -> StatusDecision:
    """Pure demotion state machine; one sweep step per call.

    Rules, evaluated in order:

    1. DEPRECATED is terminal -> unchanged.
    2. ``trust >= 0.5`` -> unchanged (no auto-promotion).
    3. ``trust < 0.5`` and ACTIVE -> PENDING (the pinned T1 contract).
    4. ``trust < 0.5`` and PENDING and no recent hit -> DEPRECATED.
    5. otherwise -> unchanged (recently-hit PENDING entry keeps its grace
       window).
    """
    if current is MemoryStatus.DEPRECATED:
        return StatusDecision(current, None)
    trust = trust_score(hit_count, corrected_count)
    if not should_demote_to_pending(trust):
        return StatusDecision(current, None)
    if current is MemoryStatus.ACTIVE:
        return StatusDecision(MemoryStatus.PENDING, ACTION_DEMOTE_PENDING)
    stale = last_hit_at is None or (now - last_hit_at).days >= no_hit_days_for_deprecation
    if stale:
        return StatusDecision(MemoryStatus.DEPRECATED, ACTION_DEPRECATE)
    return StatusDecision(current, None)


class MemoryGovernance:
    """Bypass list, status sweeps and audit writes on top of SqliteStorage."""

    def __init__(self, store: SqliteStorage) -> None:
        self._store = store

    # --- bypass -------------------------------------------------------------

    async def add_bypass(
        self, raw_name: str, reason: str, *, created_at: datetime | None = None
    ) -> BypassList:
        """Register a raw release name pattern; idempotent on pattern_hash."""
        digest = pattern_hash(raw_name)
        existing = await self.find_bypass(digest)
        if existing is not None:
            return existing
        row = BypassList(
            pattern_hash=digest,
            reason=reason,
            created_at=created_at or datetime.now(),
        )
        await self._store.add(row)
        await self.record_audit(
            operation_id=uuid4().hex,
            entity=ENTITY_BYPASS_LIST,
            action=ACTION_BYPASS_ADD,
            entity_id=row.id,
            instruction={"pattern_hash": digest, "reason": reason},
        )
        return row

    async def find_bypass(self, digest: str) -> BypassList | None:
        """The bypass row for a pattern digest, or ``None``."""
        return await self._store.find_bypass(digest)

    async def bypassed_hashes(self) -> frozenset[str]:
        """Every registered bypass pattern digest."""
        rows: Iterable[Any] = await self._store.list(BypassList)
        return frozenset(row.pattern_hash for row in rows)

    async def has_bypass(self, digest: str) -> bool:
        """DB implementation of the T1 ``MemoryStore.has_bypass`` semantics."""
        return await self.find_bypass(digest) is not None

    async def is_bypassed(self, raw_name: str) -> bool:
        """Whether a raw release name's pattern digest is registered."""
        return is_bypassed(pattern_hash(raw_name), await self.bypassed_hashes())

    # --- audit ---------------------------------------------------------------

    async def record_audit(
        self,
        *,
        operation_id: str,
        entity: str,
        action: str,
        entity_id: int | None = None,
        instruction: dict[str, object] | None = None,
        reverse: dict[str, object] | None = None,
        actor: Actor = Actor.AUTO,
    ) -> AuditLog:
        """Write one audit row; ``operation_id`` groups events into a batch."""
        row = AuditLog(
            operation_id=operation_id,
            entity=entity,
            entity_id=entity_id,
            action=action,
            instruction=instruction or {},
            reverse=reverse or {},
            actor=actor,
        )
        await self._store.add(row)
        return row

    def memory_hit_audit_row(
        self,
        *,
        operation_id: str,
        entity_id: int,
        instruction: dict[str, object] | None = None,
    ) -> AuditLog:
        """Build a hit audit row for a caller-owned transaction."""
        return memory_hit_audit_row(
            operation_id=operation_id,
            entity_id=entity_id,
            instruction=instruction,
        )

    async def record_memory_hit_audit(
        self,
        *,
        operation_id: str,
        entity_id: int,
        instruction: dict[str, object] | None = None,
    ) -> AuditLog:
        """Record one L2 memory-hit event for the T5 lookup path to call."""
        return await self.record_audit(
            operation_id=operation_id,
            entity=ENTITY_PARSE_MEMORY,
            action=ACTION_MEMORY_HIT,
            entity_id=entity_id,
            instruction=instruction or {},
        )

    # --- status sweep ---------------------------------------------------------

    async def sweep_status(
        self,
        *,
        no_hit_days_for_deprecation: int = DEFAULT_NO_HIT_DAYS_FOR_DEPRECATION,
        now: datetime | None = None,
        operation_id: str | None = None,
    ) -> StatusSweepReport:
        """Batch entry point for T6 / scheduled jobs: sweep every entry once.

        Applies :func:`status_decision` to every ParseMemory row, persists
        status changes, and writes one audit row per change under a single
        ``operation_id`` batch (generated when not supplied).
        """
        now = now or datetime.now()
        batch_id = operation_id or uuid4().hex
        demoted = 0
        deprecated = 0
        unchanged = 0
        for row in await self._store.list(ParseMemory):
            decision = status_decision(
                current=row.status,
                hit_count=row.hit_count,
                corrected_count=row.corrected_count,
                last_hit_at=row.last_hit_at,
                now=now,
                no_hit_days_for_deprecation=no_hit_days_for_deprecation,
            )
            if decision.status is row.status:
                unchanged += 1
                continue
            previous = row.status
            row.status = decision.status
            await self._store.add(row)
            if decision.action is ACTION_DEPRECATE:
                deprecated += 1
            else:
                demoted += 1
            assert decision.action is not None
            await self.record_audit(
                operation_id=batch_id,
                entity=ENTITY_PARSE_MEMORY,
                action=decision.action,
                entity_id=row.id,
                instruction={
                    "hit_count": row.hit_count,
                    "corrected_count": row.corrected_count,
                    "trust": trust_score(row.hit_count, row.corrected_count),
                },
                reverse={"status": previous.value},
            )
        return StatusSweepReport(
            operation_id=batch_id,
            demoted_to_pending=demoted,
            deprecated=deprecated,
            unchanged=unchanged,
        )
