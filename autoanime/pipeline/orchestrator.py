"""L1 -> L2 -> L3 -> arbiter orchestration (PR5 T5).

The orchestrator owns the fixed routing contract:

- L1 HIGH: direct archive path -- the result never enters L2/L3.
- L1 MEDIUM: through the L2 memory segment (the authoritative raw-name
  bypass gate runs first, then the T3 ``MemoryEnhancer``), then the L3 LLM
  segment, then the arbiter. The three-way arbitration inputs are the L1
  draft, the L1+L2 fused result and the independent L3 draft; an L2 hit
  routes ``memory``, an L2 miss routes ``l3`` -- in both cases the final
  fields come from the arbiter verdict. On an L2 miss, a best-effort
  pre-L3 disambiguation (PR7 M2/M2b) re-queries L2 under a canonical
  title: the ``title_aliases`` read side first (pure DB read, zero
  network), then the canonical title the reference chain reports; a
  canonical hit adopts the exact same memory-hit semantics as a direct
  L2 hit.
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
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol, cast
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
from autoanime.organize.expected import ExpectedContext, align_with_expected
from autoanime.pipeline.batch import (
    DEFAULT_MAX_BATCH_SIZE,
    DEFAULT_MIN_BATCH_SIZE,
    BatchItem,
    organize_batches,
)
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l2 import eligible_for_memory, pattern_hash
from autoanime.pipeline.l2.draft import apply_memory_hit
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

#: ``parse_events.level`` 的置信档位整数映射（E1 报表口径；无结果 = 0）。
_LEVEL_INT: dict[Confidence, int] = {
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
}

__all__ = [
    "AUDIT_ENTITY_ARBITER",
    "ROUTE_ARCHIVE",
    "ROUTE_L3",
    "ROUTE_MEMORY",
    "ArbiterAuditSink",
    "Orchestrator",
    "ParseEventSink",
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


class ParseEventSink(Protocol):
    """Narrow metrics persistence contract for per-pass ``parse_events`` rows.

    ``MemoryGovernance.record_parse_event`` satisfies this structurally. The
    orchestrator emits one row per full pipeline pass (single-file and batch
    entries alike) so the E1 report's llm_call_rate / by_outcome denominators
    reflect real traffic; a sink failure never breaks the parse pass.
    """

    async def record_parse_event(
        self,
        *,
        raw_name_hash: str,
        level: int,
        llm_called: bool,
        outcome: str,
        latency_ms: int | None = None,
        confidence: str | None = None,
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

    ``batch_applied`` (E1) marks a pass whose L3 segment ran through the
    opportunistic batch entry (``process_batch(batching=True)``): the
    release shared one batch LLM call with same-folder same-fansub peers.
    An item that failed inside the batch and fell back to the single-file
    retry still counts as ``batch_applied`` (it went through the batch
    entry); the flag is entry-level bookkeeping, not a success marker.
    """

    result: ParseResult | None
    route: str
    l2_applied: bool = False
    degraded: bool = False
    l3_applied: bool = False
    audit: tuple[ArbiterAudit, ...] = ()
    batch_applied: bool = False
    alignment: str | None = None
    fast_path: bool = False


@dataclass(frozen=True)
class _L2Stage:
    """One item's state after the L1+L2 segments, before the L3 segment.

    The shared intermediate between the single-file path (``process``)
    and the batch entry (``process_batch``): both run the identical L2
    stage logic (bypass gate, memory fusion, pre-L3 disambiguation) and
    only differ in how the L3 segment is executed (per item vs per batch).
    """

    base: ParseResult | None
    l1: ParseResult | None
    l2_applied: bool
    l2_degraded: bool
    memory_seasons: tuple[int, ...]
    context: ParseContext | None
    operation_id: str


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
        metrics_sink: ParseEventSink | None = None,
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
        self._metrics_sink = metrics_sink

    async def parse(
        self,
        raw: RawName,
        context: ParseContext | None = None,
        *,
        expected: ExpectedContext | None = None,
    ) -> ParseResult | None:
        """Recognizer-style shortcut: only the effective ParseResult."""
        return (await self.process(raw, context, expected=expected)).result

    async def process(
        self,
        raw: RawName,
        context: ParseContext | None = None,
        *,
        expected: ExpectedContext | None = None,
    ) -> RouteOutcome:
        """Full fixed pipeline for one release name.

        ``expected``（D13，E4）：订阅路径逐文件附带的期望上下文。L1 结果与
        expected 对齐一致（剧名命中 + 季集对上）→ HIGH 快路径：跳过 L2
        查找与 API 匹配，audit 记 ``subscribed_fast_path``；同番集数不同/
        双集/SP（``episode_variant``）与冲突（``conflict``）/解析失败
        （``unparsed``）不短路——继续走既有管线，对齐结论随 RouteOutcome
        返回，交 organize 服务做错配恢复（A/B/C）。
        """
        started = time.monotonic()
        outcome = await self._process_inner(raw, context, expected=expected)
        await self._emit_parse_event(raw, outcome, started)
        return outcome

    async def _process_inner(
        self,
        raw: RawName,
        context: ParseContext | None = None,
        *,
        expected: ExpectedContext | None = None,
    ) -> RouteOutcome:
        operation_id = uuid4().hex
        result = await self._recognizer.parse(raw, context)
        alignment_verdict: str | None = None
        if expected is not None:
            alignment = align_with_expected(result, expected)
            alignment_verdict = alignment.verdict
            if alignment.verdict == "fast_path" and result is not None:
                fast = replace(
                    result,
                    level=Confidence.HIGH,
                    confidence=max(result.confidence, 0.99),
                )
                await self._record_simple_audit(
                    operation_id,
                    action="subscribed_fast_path",
                    instruction={
                        "torrent_hash": expected.torrent_hash,
                        "episode_number": expected.episode_number,
                        "season_number": expected.season_number,
                    },
                )
                return RouteOutcome(
                    fast, ROUTE_ARCHIVE, alignment="fast_path", fast_path=True
                )
            # episode_variant/conflict/unparsed：expected 是证据之一，管线
            # 继续跑（不静默放行）；对齐结论随 outcome 交给调用方。
        if result is None:
            return replace(
                await self._finish(
                    raw, None, None, context, operation_id,
                    l2_applied=False, l2_degraded=False, memory_seasons=(),
                ),
                alignment=alignment_verdict,
            )
        if result.level is Confidence.HIGH:
            return replace(
                RouteOutcome(result, ROUTE_ARCHIVE), alignment=alignment_verdict
            )
        if not eligible_for_memory(result.level):
            # LOW: memory never participates; straight to the L3 segment.
            return replace(
                await self._finish(
                    raw, result, result, context, operation_id,
                    l2_applied=False, l2_degraded=False, memory_seasons=(),
                ),
                alignment=alignment_verdict,
            )
        outcome = await self._through_l2(raw, result, context, operation_id)
        return replace(outcome, alignment=alignment_verdict)

    async def _through_l2(
        self, raw: RawName, result: ParseResult, context: ParseContext | None,
        operation_id: str,
    ) -> RouteOutcome:
        """The L2 segment for one MEDIUM result, then the single-file L3 segment."""
        stage = await self._l2_stage(raw, result, context, operation_id)
        return await self._finish_stage_single(raw, stage)

    def _l3_wiring(
        self,
    ) -> tuple[LlmFallbackRecognizer, LlmTransport, LlmCacheStore] | None:
        """The fully wired L3 segment (recognizer, transport, cache), or ``None``.

        Single source of truth for both L3 entries: a configured-on L3 whose
        recognizer/transport/cache are all present returns the trio to use;
        L3 off or any component missing returns ``None`` (PR5 degradation
        semantics stay with the callers).
        """
        if (
            self._l3_enabled
            and self._l3_recognizer is not None
            and self._llm_transport is not None
            and self._llm_cache_store is not None
        ):
            return (self._l3_recognizer, self._llm_transport, self._llm_cache_store)
        return None

    async def _l2_stage(
        self, raw: RawName, result: ParseResult, context: ParseContext | None,
        operation_id: str,
    ) -> _L2Stage:
        """The L2 segment for one MEDIUM result, with graceful degradation.

        Shared verbatim by the single-file path and the batch entry: the
        authoritative raw-name bypass gate, the memory fusion, and the
        pre-L3 canonical disambiguation (PR7 M2/M2b). A degradation here
        never reaches L3: the L1 result is kept as the stage base.
        """
        if not self._l2_enabled:
            return _L2Stage(result, result, False, False, (), context, operation_id)
        store = self._memory_store
        if store is None:
            return _L2Stage(result, result, False, True, (), context, operation_id)
        try:
            # Authoritative raw-name bypass gate (PR4 T3 design point): a
            # bypassed release neither fuses nor records a hit. The raw name is
            # available here, not inside the L2 ParseResult contract.
            if await store.has_bypass(pattern_hash(raw.name)):
                return _L2Stage(result, result, False, False, (), context, operation_id)
        except Exception:
            return _L2Stage(result, result, False, True, (), context, operation_id)
        try:
            enhanced = await self._enhancer.enhance(
                result, context, store, operation_id=operation_id
            )
        except Exception:
            return _L2Stage(result, result, False, True, (), context, operation_id)
        if enhanced is None:
            # Miss: best-effort canonical re-query (PR7 M2), then keep the L1
            # result as-is and continue through L3.
            canonical = await self._try_canonical_memory(
                raw, result, context, operation_id
            )
            if canonical is not None:
                return canonical
            return _L2Stage(result, result, False, False, (), context, operation_id)
        return _L2Stage(
            enhanced, result, True, False,
            await self._memory_seasons(result, store), context, operation_id,
        )

    async def _try_canonical_memory(
        self,
        raw: RawName,
        result: ParseResult,
        context: ParseContext | None,
        operation_id: str,
    ) -> _L2Stage | None:
        """Pre-L3 disambiguation (PR7 M2/M2b): re-query L2 under a canonical title.

        On an L2 miss, the L1 draft's title shape is resolved to a canonical
        title through two links, cheapest first:

        1. the ``title_aliases`` read side (PR7 M2b) -- a pure DB read (the
           alias key is the draft's shape, the value the canonical shape);
           a hit costs zero network calls;
        2. the reference chain (PR7 M2, reference_cache backed, so a repeat
           query shares the L3 segment's cache and rate budget).

        When either link yields a non-empty canonical title (the alias link
        yields a canonical shape, which ``build_title_shape`` is idempotent
        on for every shape it itself produces under normal titles), the same
        two-level ``lookup_memory`` search runs with that title in place of
        the L1 draft title -- the level-1 key becomes the canonical shape and
        the level-2 key the canonical shape plus the season/episode/fansub
        the L1 draft parsed out. A hit adopts the exact memory-hit semantics
        of a direct L2 hit (``apply_memory_hit`` + hit recording +
        ``l2_applied`` stage); every degradation (no store, alias miss, no
        chain, chain miss, empty canonical title, canonical L2 miss, any
        failure) silently falls through to the next link or the original
        path. E1: returns the fused stage instead of finishing the pass so
        the batch entry can share the L2 stage verbatim.
        """
        store = self._memory_store
        if store is None:
            return None
        shape = build_title_shape(result.title)
        # Link 1 (PR7 M2b): the alias table -- pure DB read, no network. The
        # store carries the narrow ``find_alias_key`` extension (PR7 M3);
        # absent on fakes or failing, the link is silently skipped.
        canonical_shape = await self._alias_canonical_shape(store, shape)
        if canonical_shape is not None:
            outcome = await self._canonical_memory_hit(
                raw, result, canonical_shape, context, operation_id, store
            )
            if outcome is not None:
                return outcome
        # Link 2 (PR7 M2): the reference chain, behavior unchanged.
        chain = self._reference_chain
        if chain is None:
            return None
        try:
            facts = await chain.lookup(shape)
        except Exception:
            return None
        canonical_title = facts.canonical_title if facts is not None else None
        if not canonical_title:
            return None
        return await self._canonical_memory_hit(
            raw, result, canonical_title, context, operation_id, store
        )

    async def _alias_canonical_shape(
        self, store: MemoryStore, title_shape: str
    ) -> str | None:
        """The alias read side (PR7 M2b): shape -> canonical shape or ``None``.

        ``find_alias_key`` is duck-typed off the store (the ``MemoryStore``
        protocol stays PR4-narrow); a missing method or a failing query is
        logged and treated as a miss, never an error.
        """
        finder = getattr(store, "find_alias_key", None)
        if not callable(finder):
            return None
        alias_lookup = cast(
            "Callable[[str], Awaitable[str | None]]", finder
        )
        try:
            found = await alias_lookup(title_shape)
        except Exception:
            logger.warning(
                "alias lookup failed; continuing without alias", exc_info=True
            )
            return None
        return found if found else None

    async def _canonical_memory_hit(
        self,
        raw: RawName,
        result: ParseResult,
        canonical_title: str,
        context: ParseContext | None,
        operation_id: str,
        store: MemoryStore,
    ) -> _L2Stage | None:
        """Re-query L2 under a canonical title and adopt a hit (PR7 M2 semantics).

        Key derivation reuses lookup_memory's pure helpers, so the canonical
        shape comes from the single source of truth (build_title_shape) and
        the two-level order mirrors the direct L2 search exactly. A miss (or
        any failure) returns ``None`` so the caller falls through. E1: the
        fused outcome is returned as an ``_L2Stage`` (l2_applied), with the
        L3 segment left to the caller -- the batch entry needs the stage.
        """
        try:
            canonical_draft = replace(result, title=canonical_title)
            match = await lookup_memory(canonical_draft, store)
        except Exception:
            return None
        if match is None:
            return None
        try:
            enhanced = apply_memory_hit(result, match.hit)
            await store.record_hit(match.memory, operation_id=operation_id)
        except Exception:
            return None
        return _L2Stage(
            enhanced, result, True, False,
            await self._memory_seasons(canonical_draft, store), context, operation_id,
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
        L1 draft for the arbiter's three-way input. The L3 segment runs the
        single-file path (one ``enhance`` per candidate).
        """
        stage = _L2Stage(
            base, l1, l2_applied, l2_degraded, memory_seasons, context, operation_id
        )
        l3_result: ParseResult | None = None
        l3_attempted = False
        wiring = self._l3_wiring()
        if wiring is not None:
            l3_attempted = True
            recognizer, transport, cache_store = wiring
            try:
                l3_result = await recognizer.enhance(
                    raw, stage.base, context, transport, cache_store,
                    operation_id=operation_id,
                )
            except Exception:
                logger.warning(
                    "l3 segment failed unexpectedly, op=%s", operation_id, exc_info=True
                )
                l3_result = None
        return await self._complete_stage(
            raw, stage, l3_result,
            l3_attempted=l3_attempted,
            l3_degraded=(l3_result is None) if l3_attempted else self._l3_enabled,
        )

    async def _finish_stage_single(
        self, raw: RawName, stage: _L2Stage
    ) -> RouteOutcome:
        """Complete a stage through the single-file L3 segment (per-item call)."""
        return await self._finish(
            raw, stage.base, stage.l1, stage.context, stage.operation_id,
            l2_applied=stage.l2_applied, l2_degraded=stage.l2_degraded,
            memory_seasons=stage.memory_seasons,
        )

    async def _complete_stage(
        self,
        raw: RawName,
        stage: _L2Stage,
        l3_result: ParseResult | None,
        *,
        l3_attempted: bool,
        l3_degraded: bool,
        batch_applied: bool = False,
    ) -> RouteOutcome:
        """Arbitration and route assembly for one stage with a decided L3 result.

        Shared tail of the single-file path and the batch entry: the caller
        has produced (or declined to produce) the L3 draft; this method only
        routes, arbitrates and persists audit rows. ``l3_degraded`` follows
        the PR5 semantics: a configured-but-unwired L3 degrades the pass, a
        disabled L3 does not.
        """
        degraded = stage.l2_degraded or l3_degraded
        route = ROUTE_MEMORY if stage.l2_applied else ROUTE_L3
        if l3_result is None and not stage.l2_applied and not l3_attempted:
            # Nothing beyond the bare L1 result: keep it, no arbitration.
            return RouteOutcome(
                stage.base, route, degraded=degraded, batch_applied=batch_applied
            )

        verdict = await self._arbitrate(
            raw,
            l1_result=stage.l1,
            fused=stage.base if stage.l2_applied else None,
            l3_result=l3_result,
            context=stage.context,
            memory_seasons=stage.memory_seasons,
            operation_id=stage.operation_id,
        )
        result = verdict.result if verdict.result is not None else stage.base
        return RouteOutcome(
            result,
            route,
            l2_applied=stage.l2_applied,
            degraded=degraded,
            l3_applied=l3_result is not None,
            audit=verdict.audit,
            batch_applied=batch_applied,
        )

    async def process_batch(
        self,
        raws: Sequence[RawName],
        *,
        contexts: Sequence[ParseContext | None] | None = None,
        batching: bool = False,
        batch_min_size: int = DEFAULT_MIN_BATCH_SIZE,
        batch_max_size: int = DEFAULT_MAX_BATCH_SIZE,
    ) -> list[RouteOutcome]:
        """The two shared entries (9.3b): subscription quick path vs opportunistic
        batching for library imports.

        ``batching=False`` (subscription entry): every item runs the fixed
        single-file pipeline (``process``) -- one release in, one pass out,
        never waiting for a batch to form.

        ``batching=True`` (library import entry): the L1/L2 segments still
        run per item with identical semantics (bypass gate, memory fusion,
        pre-L3 disambiguation); L1 HIGH items archive directly. Only the
        candidates that reach the L3 segment are grouped by
        "same folder + same fansub" via ``organize_batches``: a group that
        naturally piled up to ``batch_min_size`` shares one batch LLM call
        (capped at ``batch_max_size`` per batch, item-level failures retried
        singly inside the L3 recognizer); the rest keep the single-file
        quick path. Output is aligned 1:1 with ``raws`` in input order.
        """
        if not raws:
            return []
        if contexts is not None and len(contexts) != len(raws):
            raise ValueError("contexts must align with raws")
        if not batching:
            ctx_list = list(contexts) if contexts is not None else [None] * len(raws)
            return [
                await self.process(raw, context)
                for raw, context in zip(raws, ctx_list, strict=True)
            ]

        total = len(raws)
        outcomes: list[RouteOutcome | None] = [None] * total
        stages: dict[int, _L2Stage] = {}
        candidates: list[int] = []
        started: dict[int, float] = {}
        for position, raw in enumerate(raws):
            started[position] = time.monotonic()
            context = contexts[position] if contexts is not None else None
            operation_id = uuid4().hex
            result = await self._recognizer.parse(raw, context)
            if result is None:
                stages[position] = _L2Stage(
                    None, None, False, False, (), context, operation_id
                )
                candidates.append(position)
                continue
            if result.level is Confidence.HIGH:
                outcomes[position] = RouteOutcome(result, ROUTE_ARCHIVE)
                continue
            if not eligible_for_memory(result.level):
                # LOW: memory never participates; straight to the L3 segment.
                stages[position] = _L2Stage(
                    result, result, False, False, (), context, operation_id
                )
                candidates.append(position)
                continue
            stages[position] = await self._l2_stage(raw, result, context, operation_id)
            candidates.append(position)

        wiring = self._l3_wiring()
        if wiring is not None:
            items: list[BatchItem] = []
            for position in candidates:
                stage = stages[position]
                candidate = raws[position]
                items.append(
                    BatchItem(
                        name=candidate.name,
                        folder=candidate.folder or candidate.parent_path,
                        fansub=stage.base.fansub if stage.base else None,
                    )
                )
            # BatchItem instances are frozen dataclasses; equal fields hash
            # equal, so the reverse mapping uses object identity -- every
            # item here is a distinct object tied to one candidate position.
            position_of_item = {
                id(item): position
                for item, position in zip(items, candidates, strict=True)
            }
            plan = organize_batches(
                items, min_batch_size=batch_min_size, max_batch_size=batch_max_size
            )
            for group in plan.groups:
                positions = [position_of_item[id(item)] for item in group.items]
                l3_results = await self._run_l3_batch(
                    wiring,
                    [raws[p] for p in positions],
                    [stages[p] for p in positions],
                    group.key[1] if group.key is not None else None,
                )
                for position, l3_result in zip(positions, l3_results, strict=True):
                    outcomes[position] = await self._complete_stage(
                        raws[position],
                        stages[position],
                        l3_result,
                        l3_attempted=True,
                        l3_degraded=l3_result is None,
                        batch_applied=True,
                    )
            for item in plan.singles:
                position = position_of_item[id(item)]
                outcomes[position] = await self._finish_stage_single(
                    raws[position], stages[position]
                )
        else:
            # L3 off or not wired: every candidate completes through the
            # single-file tail (which carries the PR5 degradation semantics).
            for position in candidates:
                outcomes[position] = await self._finish_stage_single(
                    raws[position], stages[position]
                )

        filled: list[RouteOutcome] = []
        for position, outcome in enumerate(outcomes):
            assert outcome is not None, "every input item must produce an outcome"
            await self._emit_parse_event(raws[position], outcome, started[position])
            filled.append(outcome)
        return filled

    async def _run_l3_batch(
        self,
        wiring: tuple[LlmFallbackRecognizer, LlmTransport, LlmCacheStore],
        group_raws: Sequence[RawName], group_stages: Sequence[_L2Stage],
        fansub: str | None,
    ) -> list[ParseResult | None]:
        """One batch LLM call for a same-folder same-fansub group.

        The batch prompt shares one context: the first non-None item context
        (library imports pass a uniform context; per-item contexts inside a
        batch are a backlog refinement). ``operation_id`` is the first item's
        pass id, for log correlation only -- one batch call spans several
        passes.
        """
        recognizer, transport, cache_store = wiring
        shared_context = next(
            (stage.context for stage in group_stages if stage.context is not None), None
        )
        return await recognizer.enhance_batch(
            group_raws,
            [stage.base for stage in group_stages],
            shared_context,
            transport,
            cache_store,
            fansub=fansub,
            operation_id=group_stages[0].operation_id,
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

    async def _emit_parse_event(
        self, raw: RawName, outcome: RouteOutcome, started: float
    ) -> None:
        """Emit one ``parse_events`` row for a finished pass（E1 报表写侧）。

        口径：``level`` = 置信档位整数（HIGH=3/MEDIUM=2/LOW=1/无结果=0）；
        ``llm_called`` = L3 段参与并产出 draft（``l3_applied``，缓存回放
        计入 L3 参与）；``outcome`` = 路由。写入失败只记日志，绝不向上抛
        ——识别主流程永不因指标旁路受阻。
        """
        sink = self._metrics_sink
        if sink is None:
            return
        result = outcome.result
        level = _LEVEL_INT.get(result.level, 0) if result is not None else 0
        try:
            await sink.record_parse_event(
                raw_name_hash=pattern_hash(raw.name),
                level=level,
                llm_called=outcome.l3_applied,
                outcome=outcome.route,
                latency_ms=max(int((time.monotonic() - started) * 1000), 0),
                confidence=f"{result.confidence:.4f}" if result is not None else None,
            )
        except Exception:
            logger.warning("parse event emit failed", exc_info=True)

    async def _record_simple_audit(
        self,
        operation_id: str,
        *,
        action: str,
        instruction: dict[str, object],
        entity: str = "release",
    ) -> None:
        """Persist one plain audit row (e.g. the D13 subscribed_fast_path mark)."""
        sink = self._audit_sink
        if sink is None:
            return
        try:
            await sink.record_audit(
                operation_id=operation_id,
                entity=entity,
                action=action,
                instruction=instruction,
            )
        except Exception:
            logger.warning(
                "audit write failed, op=%s action=%s", operation_id, action, exc_info=True
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
