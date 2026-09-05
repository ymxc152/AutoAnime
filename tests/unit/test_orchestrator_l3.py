"""PR5 T5：orchestrator L2→L3→arbiter 串接的单元测试（全部离线）。

- L3 优雅降级：配置关闭 / 未接线（缺 transport 或 cache）/ transport
  异常与超时重试耗尽 → 保持 L1/L2 原结果路由并标 degraded；
- L3 成功路径：超时重试后成功、schema 纠正重试、cache 命中不二次调用；
- arbiter 接线：冲突采纳高优先级来源并落 audit、L1-None 采纳 L3
  （title shape 一致升 HIGH / 不一致 MEDIUM）、L2 多季歧义被 L3 消歧、
  L2 命中而 L3 不可用时保持 fused 并记 l3_unavailable。

fake transport / fake cache / fake reference / 内存 audit sink，无网络。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import (
    MetadataReference,
    ParseContext,
    ParseResult,
    RawName,
    Registry,
)
from autoanime.pipeline.l2 import KEY_LEVEL_SERIES, key_hash, level1_key
from autoanime.pipeline.l3 import LlmCache, ReferenceChain, ReferenceFacts, llm_cache_key
from autoanime.pipeline.l3_llm import LlmFallbackRecognizer
from autoanime.pipeline.orchestrator import (
    AUDIT_ENTITY_ARBITER,
    ROUTE_ARCHIVE,
    ROUTE_L3,
    ROUTE_MEMORY,
    Orchestrator,
)

RAW_NAME = "Anime.AzurLane.Slow.Ahead.E03.1080p.Baha.WEB-DL.mkv"

VALID_RESPONSE = (
    '{"title": "Anime AzurLane Slow Ahead", "season": 2, "episode": 3, '
    '"segment": "episode", "fansub": "MWeb"}'
)


# --- fakes -------------------------------------------------------------------


@dataclass
class FakeRecognizer:
    """L1 stand-in returning one preset result."""

    result: ParseResult | None
    calls: int = 0

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        self.calls += 1
        return self.result


@dataclass
class ScriptedTransport:
    """complete() 按脚本回放响应或异常；记录每次调用。"""

    script: list[str | Exception] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str:
        self.calls.append({"prompt": prompt, "model": model, "timeout_s": timeout_s})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@dataclass
class MemoryCacheStore:
    """llm_cache 的内存 fake。"""

    entries: dict[str, LlmCache] = field(default_factory=dict)

    async def get(self, pattern_hash: str) -> LlmCache | None:
        return self.entries.get(pattern_hash)

    async def put(self, cache: LlmCache) -> None:
        self.entries[cache.pattern_hash] = cache


@dataclass
class FakeAuditSink:
    """record_audit 的内存 fake（governance.record_audit 窄接口形状）。"""

    rows: list[dict[str, Any]] = field(default_factory=list)

    async def record_audit(
        self,
        *,
        operation_id: str,
        entity: str,
        action: str,
        instruction: dict[str, object] | None = None,
    ) -> object:
        self.rows.append(
            {
                "operation_id": operation_id,
                "entity": entity,
                "action": action,
                "instruction": dict(instruction or {}),
            }
        )
        return None


@dataclass
class FakeReference:
    """MetadataReference fake：恒返回预置 ReferenceFacts。"""

    facts: ReferenceFacts | None
    lookups: list[str] = field(default_factory=list)

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        self.lookups.append(title_shape)
        return self.facts


@dataclass
class FakeMemoryRow:
    key_level: int
    key_hash: str
    result: dict[str, object] = field(default_factory=dict)
    hit_count: int = 0
    corrected_count: int = 0
    status: str = "active"
    title_shape: str | None = None


class FakeMemoryStore:
    """In-memory ``MemoryStore`` fake: rows keyed by (key_level, key_hash)."""

    def __init__(self, *rows: FakeMemoryRow) -> None:
        self._rows = {(row.key_level, row.key_hash): row for row in rows}
        self.recorded_hits: list[Any] = []

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None:
        return self._rows.get((key_level, key_hash))

    async def record_hit(self, parse_memory: Any, *, operation_id: str | None = None) -> None:
        self.recorded_hits.append(parse_memory)

    async def record_correction(self, parse_memory: Any) -> None:
        return None

    async def has_bypass(self, pattern_hash: str) -> bool:
        return False


# --- helpers -----------------------------------------------------------------


def _raw() -> RawName:
    return RawName(name=RAW_NAME)


def _medium(season: int | None = None) -> ParseResult:
    """L1 MEDIUM shape: fansub missing (and season unless overridden)."""
    evidence = {
        "title": "name",
        "season": "name" if season is not None else "none",
        "episode": "name",
        "segment": "name",
        "fansub": "none",
    }
    return ParseResult(
        title="Anime AzurLane Slow Ahead",
        season=season,
        episode=3,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=() if season is not None else ("season",),
        evidence=evidence,
    )


def _high() -> ParseResult:
    return ParseResult(
        title="Anime AzurLane Slow Ahead",
        season=2,
        episode=3,
        segment=Segment.EPISODE,
        fansub="MWeb",
        level=Confidence.HIGH,
        confidence=1.0,
        missing_fields=(),
        evidence={"title": "name", "season": "name", "episode": "name", "segment": "name", "fansub": "name"},
    )


def _orchestrator(
    recognizer: ParseResult | None,
    *,
    l3_enabled: bool = True,
    transport: ScriptedTransport | None = None,
    cache: MemoryCacheStore | None = None,
    audit_sink: FakeAuditSink | None = None,
    reference_chain: ReferenceChain | None = None,
    memory_store: FakeMemoryStore | None = None,
) -> Orchestrator:
    return Orchestrator(
        FakeRecognizer(recognizer),
        memory_store=memory_store,
        l3_enabled=l3_enabled,
        l3_recognizer=LlmFallbackRecognizer(model="test-model"),
        llm_transport=transport,
        llm_cache_store=cache,
        reference_chain=reference_chain,
        audit_sink=audit_sink,
    )


def _memory_row(**result: Any) -> FakeMemoryRow:
    """A series-level ACTIVE row under the default title's level-1 key."""
    title = result.get("title", "Anime AzurLane Slow Ahead")
    assert isinstance(title, str)
    return FakeMemoryRow(
        key_level=KEY_LEVEL_SERIES,
        key_hash=key_hash(level1_key(title)),
        result=result,
    )


def _audit_actions(sink: FakeAuditSink) -> list[str]:
    return [row["action"] for row in sink.rows]


def _assert_entities_arbiter(sink: FakeAuditSink) -> None:
    assert sink.rows
    assert all(row["entity"] == AUDIT_ENTITY_ARBITER for row in sink.rows)
    assert len({row["operation_id"] for row in sink.rows}) == 1
    assert all(row["operation_id"] for row in sink.rows)


# --- L3 graceful degradation -------------------------------------------------


async def test_l3_disabled_by_config_skips_segment_without_degrading() -> None:
    transport = ScriptedTransport()
    cache = MemoryCacheStore()
    l1 = _medium()
    outcome = await _orchestrator(
        l1, l3_enabled=False, transport=transport, cache=cache, memory_store=FakeMemoryStore()
    ).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.result == l1
    assert outcome.degraded is False
    assert outcome.l3_applied is False
    assert transport.calls == []


async def test_l3_enabled_without_transport_degrades() -> None:
    l1 = _medium()
    outcome = await _orchestrator(l1, transport=None, cache=MemoryCacheStore()).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.result == l1
    assert outcome.degraded is True
    assert outcome.l3_applied is False


async def test_transport_error_exhausting_retries_degrades() -> None:
    # LLM_MAX_RETRIES=2: two failed attempts, then the segment gives up.
    transport = ScriptedTransport([RuntimeError("boom"), RuntimeError("boom")])
    l1 = _medium()
    outcome = await _orchestrator(
        l1, transport=transport, cache=MemoryCacheStore()
    ).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.result == l1
    assert outcome.degraded is True
    assert len(transport.calls) == 2


async def test_timeout_retry_then_success_fills_and_upgrades() -> None:
    transport = ScriptedTransport([TimeoutError("timed out"), VALID_RESPONSE])
    cache = MemoryCacheStore()
    outcome = await _orchestrator(
        _medium(), transport=transport, cache=cache, memory_store=FakeMemoryStore()
    ).process(_raw())

    assert outcome.degraded is False
    assert outcome.l3_applied is True
    assert outcome.route == ROUTE_L3
    assert outcome.result is not None
    assert outcome.result.season == 2
    assert outcome.result.fansub == "MWeb"
    assert outcome.result.evidence["season"] == "llm"
    assert outcome.result.level is Confidence.HIGH  # R5 verified (title shape)
    assert len(transport.calls) == 2
    # 真实调用只计一次，成功响应写入 cache。
    assert len(cache.entries) == 1


async def test_schema_correction_retry_succeeds() -> None:
    transport = ScriptedTransport(['{"title": 42}', VALID_RESPONSE])
    outcome = await _orchestrator(
        _medium(), transport=transport, cache=MemoryCacheStore(), memory_store=FakeMemoryStore()
    ).process(_raw())

    assert outcome.degraded is False
    assert outcome.l3_applied is True
    assert outcome.result is not None
    assert outcome.result.season == 2
    assert len(transport.calls) == 2


# --- arbiter wiring ----------------------------------------------------------


async def test_arbiter_conflict_keeps_higher_evidence_and_audits() -> None:
    # L1 decided season=1 from the name; the LLM claims season=2: name wins.
    transport = ScriptedTransport([VALID_RESPONSE])
    sink = FakeAuditSink()
    outcome = await _orchestrator(
        _medium(season=1), transport=transport, cache=MemoryCacheStore(), audit_sink=sink
    ).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.result is not None
    assert outcome.result.season == 1
    assert outcome.result.evidence["season"] == "name"
    assert outcome.result.fansub == "MWeb"  # absent field still filled by L3
    assert _audit_actions(sink) == ["field_conflict"]
    conflict = sink.rows[0]
    assert conflict["instruction"]["field"] == "season"
    assert conflict["instruction"]["l1_value"] == 1
    assert conflict["instruction"]["l3_value"] == 2
    _assert_entities_arbiter(sink)


async def test_l1_none_adopts_l3_as_medium() -> None:
    transport = ScriptedTransport([VALID_RESPONSE])
    outcome = await _orchestrator(
        None, transport=transport, cache=MemoryCacheStore()
    ).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.result is not None
    assert outcome.result.title == "Anime AzurLane Slow Ahead"
    assert outcome.result.season == 2
    assert outcome.result.level is Confidence.MEDIUM  # no reference: base MEDIUM
    assert set(outcome.result.evidence.values()) == {"llm"}


async def test_l1_none_with_reference_verified_title_raises_high() -> None:
    registry = Registry()
    provider = FakeReference(
        ReferenceFacts(canonical_title="Anime AzurLane Slow Ahead", source="fake")
    )
    registry.register(MetadataReference, "fake")(provider)
    chain = ReferenceChain(registry, order=("fake",), enabled=True)
    transport = ScriptedTransport([VALID_RESPONSE])
    sink = FakeAuditSink()
    outcome = await _orchestrator(
        None,
        transport=transport,
        cache=MemoryCacheStore(),
        reference_chain=chain,
        audit_sink=sink,
    ).process(_raw())

    assert outcome.result is not None
    assert outcome.result.level is Confidence.HIGH
    assert provider.lookups  # reference consulted for the below-HIGH candidate
    assert _audit_actions(sink) == ["level_upgraded"]
    _assert_entities_arbiter(sink)


async def test_l1_none_with_mismatched_reference_keeps_medium() -> None:
    registry = Registry()
    provider = FakeReference(ReferenceFacts(canonical_title="Totally Other Show", source="fake"))
    registry.register(MetadataReference, "fake")(provider)
    chain = ReferenceChain(registry, order=("fake",), enabled=True)
    outcome = await _orchestrator(
        None,
        transport=ScriptedTransport([VALID_RESPONSE]),
        cache=MemoryCacheStore(),
        reference_chain=chain,
    ).process(_raw())

    assert outcome.result is not None
    assert outcome.result.level is Confidence.MEDIUM


async def test_l2_multi_season_ambiguity_disambiguated_by_l3() -> None:
    # memory 行 seasons=[1,2] 多值歧义：融合时 season 缺失，L3 给出 2 → R6 消歧。
    store = FakeMemoryStore(
        _memory_row(
            title="Anime AzurLane Slow Ahead",
            seasons=[1, 2],
            episode=None,
            segment="season_pack",
            fansub="MWeb",
        )
    )
    transport = ScriptedTransport([VALID_RESPONSE])
    sink = FakeAuditSink()
    outcome = await _orchestrator(
        _medium(),
        transport=transport,
        cache=MemoryCacheStore(),
        memory_store=store,
        audit_sink=sink,
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert outcome.l3_applied is True
    assert outcome.degraded is False
    assert outcome.result is not None
    assert outcome.result.season == 2
    assert outcome.result.evidence["season"] == "llm"
    assert len(store.recorded_hits) == 1
    assert "season_disambiguated" in _audit_actions(sink)


async def test_l2_hit_with_l3_unavailable_keeps_fused_and_audits() -> None:
    store = FakeMemoryStore(
        _memory_row(
            title="Anime AzurLane Slow Ahead",
            season=2,
            episode=None,
            segment="season_pack",
            fansub="MWeb",
        )
    )
    sink = FakeAuditSink()
    outcome = await _orchestrator(
        _medium(), transport=None, cache=MemoryCacheStore(), memory_store=store, audit_sink=sink
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert outcome.l3_applied is False
    assert outcome.degraded is True
    assert outcome.result is not None
    assert outcome.result.season == 2
    assert outcome.result.level is Confidence.HIGH  # fused result untouched (R7)
    assert _audit_actions(sink) == ["l3_unavailable"]
    _assert_entities_arbiter(sink)


async def test_cache_hit_skips_transport_on_repeat_parses() -> None:
    cache = MemoryCacheStore()
    cache.entries[llm_cache_key(RAW_NAME)] = LlmCache(
        pattern_hash=llm_cache_key(RAW_NAME), response=VALID_RESPONSE, model="test-model"
    )
    transport = ScriptedTransport()  # any call would consume an empty script
    orchestrator = _orchestrator(_medium(), transport=transport, cache=cache)

    first = await orchestrator.process(_raw())
    second = await orchestrator.process(_raw())

    assert first.result is not None and second.result is not None
    assert first.result.season == 2 and second.result.season == 2
    assert first.route == ROUTE_L3 and second.route == ROUTE_L3
    assert transport.calls == []


async def test_high_result_never_enters_l2_or_l3() -> None:
    transport = ScriptedTransport()
    store = FakeMemoryStore()
    outcome = await _orchestrator(
        _high(), transport=transport, cache=MemoryCacheStore(), memory_store=store
    ).process(_raw())

    assert outcome.route == ROUTE_ARCHIVE
    assert outcome.degraded is False
    assert outcome.l3_applied is False
    assert transport.calls == []
    assert store.recorded_hits == []


async def test_l2_miss_with_l3_failure_audits_l3_unavailable() -> None:
    transport = ScriptedTransport([RuntimeError("down"), RuntimeError("down")])
    sink = FakeAuditSink()
    outcome = await _orchestrator(
        _medium(), transport=transport, cache=MemoryCacheStore(), audit_sink=sink
    ).process(_raw())

    assert outcome.degraded is True
    assert outcome.result == _medium()
    assert _audit_actions(sink) == ["l3_unavailable"]
    _assert_entities_arbiter(sink)
