"""L1 -> L2 -> L3 -> arbiter orchestration (PR5 T5).

The orchestrator owns the fixed routing contract:

- L1 HIGH: direct archive path -- the result never enters L2/L3.
- L1 MEDIUM: through the L2 memory segment (the authoritative raw-name
  bypass gate runs first, then the T3 ``MemoryEnhancer``), then the L3 LLM
  segment, then the arbiter. The three-way arbitration inputs are the L1
  draft, the L1+L2 fused result and the independent L3 draft; an L2 hit
  routes ``memory``, an L2 miss routes ``l3`` -- in both cases the final
  fields come from the arbiter verdict.
- L1 LOW and L1 ``None``: straight to the L3 segment (route ``l3``).

Graceful degradation (PR4/PR5 contract):

- L2 configured off or the memory store unavailable: the L1-only result
  continues to L3; an L2-configured-on-but-broken pass reports
  ``degraded`` (PR4 semantics, unchanged).
- L3 configured off: skipped entirely, never ``degraded``.
- L3 configured on but not fully wired (no recognizer/transport/cache
  store) or the segment failing (transport errors, timeouts, retries
  exhausted, schema corrections exhausted): the L1/L2 result is kept
  unchanged and the outcome is marked ``degraded``.
- The arbiter never lowers a result: a kept L1/L2 result preserves its
  route (``memory`` for an L2 hit, ``l3`` otherwise).

Arbiter audit rows (R8) are persisted through the narrow
``ArbiterAuditSink`` protocol (structurally satisfied by
``MemoryGovernance.record_audit``), batched under the per-pass
``operation_id``; audit write failures are logged, never fatal.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from autoanime.core.enums import Confidence
from autoanime.core.interfaces import (
    LlmCacheStore,
    LlmTransport,
    MemoryStore,
    ParseContext,
    ParseResult,
    RawName,
    Recognizer,
)
from autoanime.memory.lookup import lookup_memory
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l2 import eligible_for_memory, pattern_hash
from autoanime.pipeline.l2.placeholders import build_title_shape
from autoanime.pipeline.l2_memory import MemoryEnhancer
from autoanime.pipeline.l3 import (
    ArbiterAudit,
    ArbiterInput,
    ArbiterVerdict,
    ReferenceChain,
    ReferenceFacts,
    arbitrate,
)
from autoanime.pipeline.l3_llm import LlmFallbackRecognizer

logger = logging.getLogger(__name__)

ROUTE_ARCHIVE = "archive"
ROUTE_MEMORY = "memory"
ROUTE_L3 = "l3"

#: ``audit_log.entity`` for arbiter verdict rows (R8 batch persistence).
AUDIT_ENTITY_ARBITER = "arbiter"

__all__ = [
    "AUDIT_ENTITY_ARBITER",
    "ROUTE_ARCHIVE",
    "ROUTE_L3",
    "ROUTE_MEMORY",
    "ArbiterAuditSink",
    "Orchestrator",
    "RouteOutcome",
]


class ArbiterAuditSink(Protocol):
    """Narrow audit persistence contract for arbiter verdict rows (R8).

    ``MemoryGovernance.record_audit`` satisfies this structurally; tests may
    record rows in memory instead. The orchestrator never touches governance
    internals, and an audit failure never breaks the parse pass.
    """

    async def record_audit(
        self,
        *,
        operation_id: str,
        entity: str,
        action: str,
        instruction: dict[str, object] | None = None,
    ) -> object: ...


@dataclass(frozen=True)
class RouteOutcome:
    """One full pipeline pass: the effective result and the route it took.

    ``route`` is ``archive`` (L1 HIGH, direct archive path), ``memory``
    (an L2 memory hit participated) or ``l3`` (the L3 segment: L1 miss,
    LOW, or a MEDIUM result memory could not enhance). ``l2_applied``
    marks a fused memory hit; ``l3_applied`` marks a pass where the L3
    segment produced a draft the arbiter merged; ``audit`` carries the
    arbiter's R8 rows for the pass. ``degraded`` marks a pass where a
    configured-on capability (L2 store or L3) was unavailable, so the
    pre-existing L1/L2 fallback produced the outcome.
    """

    result: ParseResult | None
    route: str
    l2_applied: bool = False
    degraded: bool = False
    l3_applied: bool = False
    audit: tuple[ArbiterAudit, ...] = ()


class Orchestrator:
    """Run L1, the L2 memory segment, the L3 LLM segment and the arbiter."""

    def __init__(
        self,
        recognizer: Recognizer | None = None,
        *,
        memory_store: MemoryStore | None = None,
        l2_enabled: bool = True,
        l3_enabled: bool = False,
        l3_recognizer: LlmFallbackRecognizer | None = None,
        llm_transport: LlmTransport | None = None,
        llm_cache_store: LlmCacheStore | None = None,
        reference_chain: ReferenceChain | None = None,
        audit_sink: ArbiterAuditSink | None = None,
    ) -> None:
        self._recognizer = recognizer if recognizer is not None else LocalRecognizer()
        self._memory_store = memory_store
        self._l2_enabled = l2_enabled
        self._enhancer = MemoryEnhancer()
        self._l3_enabled = l3_enabled
        self._l3_recognizer = l3_recognizer
        self._llm_transport = llm_transport
        self._llm_cache_store = llm_cache_store
        self._reference_chain = reference_chain
        self._audit_sink = audit_sink

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        """Recognizer-style shortcut: only the effective ParseResult."""
        return (await self.process(raw, context)).result

    async def process(
        self, raw: RawName, context: ParseContext | None = None
    ) -> RouteOutcome:
        """Full fixed pipeline for one release name."""
        operation_id = uuid4().hex
        result = await self._recognizer.parse(raw, context)
        if result is None:
            return await self._finish(
                raw, None, None, context, operation_id,
                l2_applied=False, l2_degraded=False, memory_seasons=(),
            )
        if result.level is Confidence.HIGH:
            return RouteOutcome(result, ROUTE_ARCHIVE)
        if not eligible_for_memory(result.level):
            # LOW: memory never participates; straight to the L3 segment.
            return await self._finish(
                raw, result, result, context, operation_id,
                l2_applied=False, l2_degraded=False, memory_seasons=(),
            )
        return await self._through_l2(raw, result, context, operation_id)

    async def _through_l2(
        self, raw: RawName, result: ParseResult, context: ParseContext | None,
        operation_id: str,
    ) -> RouteOutcome:
        """The L2 segment for one MEDIUM result, with graceful degradation."""
        if not self._l2_enabled:
            return await self._finish(
                raw, result, result, context, operation_id,
                l2_applied=False, l2_degraded=False, memory_seasons=(),
            )
        store = self._memory_store
        if store is None:
            return await self._finish(
                raw, result, result, context, operation_id,
                l2_applied=False, l2_degraded=True, memory_seasons=(),
            )
        try:
            # Authoritative raw-name bypass gate (PR4 T3 design point): a
            # bypassed release neither fuses nor records a hit. The raw name is
            # available here, not inside the L2 ParseResult contract.
            if await store.has_bypass(pattern_hash(raw.name)):
                return await self._finish(
                    raw, result, result, context, operation_id,
                    l2_applied=False, l2_degraded=False, memory_seasons=(),
                )
        except Exception:
            return await self._finish(
                raw, result, result, context, operation_id,
                l2_applied=False, l2_degraded=True, memory_seasons=(),
            )
        try:
            enhanced = await self._enhancer.enhance(
                result, context, store, operation_id=operation_id
            )
        except Exception:
            return await self._finish(
                raw, result, result, context, operation_id,
                l2_applied=False, l2_degraded=True, memory_seasons=(),
            )
        if enhanced is None:
            # Miss: keep the L1 result as-is and continue through L3.
            return await self._finish(
                raw, result, result, context, operation_id,
                l2_applied=False, l2_degraded=False, memory_seasons=(),
            )
        return await self._finish(
            raw, enhanced, result, context, operation_id,
            l2_applied=True, l2_degraded=False,
            memory_seasons=await self._memory_seasons(result, store),
        )

    async def _finish(
        self,
        raw: RawName,
        base: ParseResult | None,
        l1: ParseResult | None,
        context: ParseContext | None,
        operation_id: str,
        *,
        l2_applied: bool,
        l2_degraded: bool,
        memory_seasons: tuple[int, ...],
    ) -> RouteOutcome:
        """The L3 segment and arbitration for one below-HIGH candidate.

        ``base`` is the pre-L3 effective result (the L1+L2 fusion on a
        memory hit, else the L1 result or ``None``); ``l1`` is the original
        L1 draft for the arbiter's three-way input.
        """
        degraded = l2_degraded
        l3_result: ParseResult | None = None
        l3_attempted = False
        if (
            self._l3_enabled
            and self._l3_recognizer is not None
            and self._llm_transport is not None
            and self._llm_cache_store is not None
        ):
            l3_attempted = True
            try:
                l3_result = await self._l3_recognizer.enhance(
                    raw, base, context, self._llm_transport, self._llm_cache_store,
                    operation_id=operation_id,
                )
            except Exception:
                logger.warning(
                    "l3 segment failed unexpectedly, op=%s", operation_id, exc_info=True
                )
                l3_result = None
            if l3_result is None:
                degraded = True
        elif self._l3_enabled:
            # Configured on but not fully wired: recognizer/transport/cache
            # missing, so L3 cannot run this pass.
            degraded = True

        route = ROUTE_MEMORY if l2_applied else ROUTE_L3
        if l3_result is None and not l2_applied and not l3_attempted:
            # Nothing beyond the bare L1 result: keep it, no arbitration.
            return RouteOutcome(base, route, degraded=degraded)

        verdict = await self._arbitrate(
            raw,
            l1_result=l1,
            fused=base if l2_applied else None,
            l3_result=l3_result,
            context=context,
            memory_seasons=memory_seasons,
            operation_id=operation_id,
        )
        result = verdict.result if verdict.result is not None else base
        return RouteOutcome(
            result,
            route,
            l2_applied=l2_applied,
            degraded=degraded,
            l3_applied=l3_result is not None,
            audit=verdict.audit,
        )

    async def _arbitrate(
        self,
        raw: RawName,
        *,
        l1_result: ParseResult | None,
        fused: ParseResult | None,
        l3_result: ParseResult | None,
        context: ParseContext | None,
        memory_seasons: tuple[int, ...],
        operation_id: str,
    ) -> ArbiterVerdict:
        """Three-way arbitration plus R8 audit persistence for the pass."""
        reference = await self._reference_facts(
            fused if fused is not None else l1_result, l3_result
        )
        verdict = arbitrate(
            ArbiterInput(
                raw=raw,
                l1_result=l1_result,
                fused=fused,
                l3_result=l3_result,
                context=context,
                reference=reference,
                memory_seasons=memory_seasons,
                operation_id=operation_id,
            )
        )
        await self._record_audit(verdict, operation_id)
        return verdict

    async def _reference_facts(
        self, base: ParseResult | None, l3: ParseResult | None
    ) -> ReferenceFacts | None:
        """Reference-chain lookup for candidates below HIGH; failures yield None."""
        chain = self._reference_chain
        if chain is None:
            return None
        candidate = base if base is not None else l3
        if candidate is None or candidate.level is Confidence.HIGH:
            return None
        try:
            return await chain.lookup(build_title_shape(candidate.title))
        except Exception:
            logger.warning(
                "reference chain lookup failed; continuing without facts", exc_info=True
            )
            return None

    async def _memory_seasons(
        self, l1: ParseResult, store: MemoryStore
    ) -> tuple[int, ...]:
        """The consumed memory row's seasons list (R6 disambiguation input).

        ``enhance_result`` consumes a row and returns only the fused result,
        so the orchestrator re-derives the match with the same pure lookup
        (series level first, no hit recording). Any failure yields ``()``
        (no disambiguation input), never an error.
        """
        try:
            match = await lookup_memory(l1, store)
        except Exception:
            logger.warning(
                "memory seasons re-lookup failed; no disambiguation input",
                exc_info=True,
            )
            return ()
        if match is None:
            return ()
        stored = getattr(match.memory, "result", None)
        if not isinstance(stored, Mapping):
            return ()
        seasons = stored.get("seasons")
        if not isinstance(seasons, list):
            return ()
        return tuple(
            season
            for season in seasons
            if isinstance(season, int) and not isinstance(season, bool)
        )

    async def _record_audit(self, verdict: ArbiterVerdict, operation_id: str) -> None:
        """Persist the verdict's audit rows under the pass operation_id."""
        sink = self._audit_sink
        if sink is None:
            return
        for audit in verdict.audit:
            try:
                await sink.record_audit(
                    operation_id=operation_id,
                    entity=AUDIT_ENTITY_ARBITER,
                    action=audit.action,
                    instruction=dict(audit.detail),
                )
            except Exception:
                logger.warning(
                    "arbiter audit write failed, op=%s", operation_id, exc_info=True
                )
                return
