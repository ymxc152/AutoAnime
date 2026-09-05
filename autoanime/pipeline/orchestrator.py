"""Fixed L1 -> L2 -> L3 -> arbitration orchestration.

The orchestrator owns the PR4 routing contract as a fixed pipeline (not a
registry entry):

- L1 HIGH: direct archive path -- the result never enters L2.
- L1 MEDIUM: through the L2 memory segment. The authoritative raw-name
  bypass gate runs first (the orchestrator holds the ``RawName``; the query
  side only sees the parsed title), then the T3 ``MemoryEnhancer``. A
  consumed hit returns the fused result (route ``memory``); a miss keeps
  the L1 result unchanged -- both continue downstream to the L3 placeholder.
- L1 LOW and L1 ``None``: straight to the L3 placeholder.

Graceful degradation (PR4 contract): when L2 is switched off by
configuration, or the memory store is unavailable (never injected, or any
L2 call raises), every input takes the original L1-only routing and the
pipeline never crashes on L2. A degraded pass is reported through
``RouteOutcome.degraded`` instead of an error.

L3 and arbitration stay placeholders in this slice: the orchestrator only
labels the route each result takes; no L3 logic is invoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from autoanime.core.enums import Confidence
from autoanime.core.interfaces import (
    MemoryStore,
    ParseContext,
    ParseResult,
    RawName,
    Recognizer,
)
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l2 import eligible_for_memory, pattern_hash
from autoanime.pipeline.l2_memory import MemoryEnhancer

ROUTE_ARCHIVE = "archive"
ROUTE_MEMORY = "memory"
ROUTE_L3 = "l3"

__all__ = [
    "ROUTE_ARCHIVE",
    "ROUTE_L3",
    "ROUTE_MEMORY",
    "Orchestrator",
    "RouteOutcome",
]


@dataclass(frozen=True)
class RouteOutcome:
    """One L1(+L2) pass: the effective result and the route it takes next.

    ``route`` is ``archive`` (L1 HIGH, direct archive path), ``memory``
    (an L2 memory hit was fused into an L1 MEDIUM result) or ``l3`` (the L3
    placeholder: L1 miss, LOW, or a MEDIUM result memory could not enhance).
    ``l2_applied`` marks a fused memory hit; ``degraded`` marks a pass where
    L2 was configured on but unavailable, so the L1-only fallback produced
    the outcome.
    """

    result: ParseResult | None
    route: str
    l2_applied: bool = False
    degraded: bool = False


class Orchestrator:
    """Run L1, then the L2 memory segment for MEDIUM results, and route."""

    def __init__(
        self,
        recognizer: Recognizer | None = None,
        *,
        memory_store: MemoryStore | None = None,
        l2_enabled: bool = True,
    ) -> None:
        self._recognizer = recognizer if recognizer is not None else LocalRecognizer()
        self._memory_store = memory_store
        self._l2_enabled = l2_enabled
        self._enhancer = MemoryEnhancer()

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        """Recognizer-style shortcut: only the effective ParseResult."""
        return (await self.process(raw, context)).result

    async def process(
        self, raw: RawName, context: ParseContext | None = None
    ) -> RouteOutcome:
        """Full fixed pipeline for one release name."""
        result = await self._recognizer.parse(raw, context)
        if result is None:
            return RouteOutcome(result=None, route=ROUTE_L3)
        if result.level is Confidence.HIGH:
            return RouteOutcome(result, ROUTE_ARCHIVE)
        if not eligible_for_memory(result.level):
            # LOW: memory never participates; straight to the L3 placeholder.
            return RouteOutcome(result, ROUTE_L3)
        return await self._through_l2(raw, result, context)

    async def _through_l2(
        self, raw: RawName, result: ParseResult, context: ParseContext | None
    ) -> RouteOutcome:
        """The L2 segment for one MEDIUM result, with graceful degradation."""
        if not self._l2_enabled:
            return RouteOutcome(result, ROUTE_L3)
        store = self._memory_store
        if store is None:
            return RouteOutcome(result, ROUTE_L3, degraded=True)
        try:
            # Authoritative raw-name bypass gate (PR4 T3 design point): a
            # bypassed release neither fuses nor records a hit. The raw name is
            # available here, not inside the L2 ParseResult contract.
            if await store.has_bypass(pattern_hash(raw.name)):
                return RouteOutcome(result, ROUTE_L3)
        except Exception:
            return RouteOutcome(result, ROUTE_L3, degraded=True)
        try:
            enhanced = await self._enhancer.enhance(
                result, context, store, operation_id=uuid4().hex
            )
        except Exception:
            return RouteOutcome(result, ROUTE_L3, degraded=True)
        if enhanced is None:
            # Miss: keep the L1 result as-is and route the L3 placeholder.
            return RouteOutcome(result, ROUTE_L3)
        return RouteOutcome(enhanced, ROUTE_MEMORY, l2_applied=True)
