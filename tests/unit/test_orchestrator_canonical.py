"""PR7 M2：orchestrator 前置消歧段（L2 miss 后以 canonical title 重查 L2）。

- 命中路径：参考链报告非空 ``canonical_title`` → 以 canonical shape 派生
  两级 key 重查 L2 → 命中即按原 L2 命中的完全相同语义产出（enhance 应用、
  evidence=memory、route=memory），L3 不再作为 fallback 路由；
- 降级矩阵（全部静默走原路径进 L3，零行为差异、不抛错）：参考链为
  ``None`` / reference 关闭 / 链 lookup 返回 ``None``（含负缓存）/
  ``canonical_title`` 为空 / canonical 重查两级都 miss / 链 lookup 抛异常
  / canonical 重查时 store 抛异常；
- 缓存共享：消歧查询经 reference_cache，与 arbiter 段的参考查询共享同一
  缓存，上游 provider 至多外呼一次。

全部离线 fake：fake 识别器、内存 memory store、fake 参考源，无网络。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import (
    MetadataReference,
    ParseContext,
    ParseResult,
    RawName,
    Registry,
)
from autoanime.core.models import ReferenceCache
from autoanime.memory.reference_cache import CachedReference
from autoanime.pipeline.l2 import (
    KEY_LEVEL_EXACT,
    KEY_LEVEL_SERIES,
    key_hash,
    level1_key,
    level2_key,
)
from autoanime.pipeline.l2.placeholders import build_title_shape
from autoanime.pipeline.l3 import LlmCache, ReferenceChain, ReferenceFacts
from autoanime.pipeline.orchestrator import (
    ROUTE_L3,
    ROUTE_MEMORY,
    Orchestrator,
)

ROMAJI = "Kono Subarashii Sekai ni Shukufuku wo"
CANONICAL = "この素晴らしい世界に祝福を"
RAW_NAME = "[MSubs] Kono Subarashii Sekai ni Shukufuku wo - 03 (1080p).mkv"

VALID_RESPONSE = (
    '{"title": "この素晴らしい世界に祝福を", "season": 2, "episode": 3, '
    '"segment": "episode", "fansub": "MSubs"}'
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
    calls: list[str] = field(default_factory=list)

    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str:
        self.calls.append(prompt)
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
class FakeMemoryRow:
    key_level: int
    key_hash: str
    result: dict[str, object] = field(default_factory=dict)
    hit_count: int = 0
    corrected_count: int = 0
    status: str = "active"
    title_shape: str | None = None


class FakeMemoryStore:
    """In-memory ``MemoryStore`` fake; ``fail_after`` 模拟重查时 store 故障.

    ``alias_map``/``alias_error`` 模拟 PR7 M3 ``find_alias_key`` 读侧（M2b
    接线用）；不传时行为与纯 PR4 store 一致。
    """

    def __init__(
        self,
        *rows: FakeMemoryRow,
        fail_after: int | None = None,
        alias_map: dict[str, str] | None = None,
        alias_error: Exception | None = None,
        alias_rows: dict[str, tuple[str, str | None]] | None = None,
    ) -> None:
        self._rows = {(row.key_level, row.key_hash): row for row in rows}
        self.recorded_hits: list[Any] = []
        self.lookup_calls = 0
        self._fail_after = fail_after
        self._alias_map = alias_map if alias_map is not None else {}
        self._alias_error = alias_error
        # A1'：带 source 的 alias 行（manual = 用户 confirm 写下的映射）。
        self._alias_rows = alias_rows if alias_rows is not None else {}
        self.alias_lookups: list[str] = []

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None:
        self.lookup_calls += 1
        if self._fail_after is not None and self.lookup_calls > self._fail_after:
            raise RuntimeError("store unavailable")
        return self._rows.get((key_level, key_hash))

    async def find_alias_key(self, title_shape_norm: str) -> str | None:
        self.alias_lookups.append(title_shape_norm)
        if self._alias_error is not None:
            raise self._alias_error
        if title_shape_norm in self._alias_rows:
            return self._alias_rows[title_shape_norm][0]
        return self._alias_map.get(title_shape_norm)

    async def find_alias_row(
        self, title_shape_norm: str
    ) -> tuple[str, str | None] | None:
        self.alias_lookups.append(title_shape_norm)
        if self._alias_error is not None:
            raise self._alias_error
        # alias_map 视为窄口径（source 未知）映射；alias_rows 是带 source 行。
        if title_shape_norm in self._alias_rows:
            return self._alias_rows[title_shape_norm]
        if title_shape_norm in self._alias_map:
            return (self._alias_map[title_shape_norm], None)
        return None

    async def record_hit(
        self, parse_memory: Any, *, operation_id: str | None = None
    ) -> None:
        self.recorded_hits.append(parse_memory)

    async def record_correction(self, parse_memory: Any) -> None:
        return None

    async def has_bypass(self, pattern_hash: str) -> bool:
        return False


@dataclass
class FakeReferenceProvider:
    """MetadataReference fake：canned shape→facts 映射，可选恒抛异常。"""

    facts_by_shape: dict[str, ReferenceFacts] = field(default_factory=dict)
    error: Exception | None = None
    lookups: list[str] = field(default_factory=list)

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        self.lookups.append(title_shape)
        if self.error is not None:
            raise self.error
        return self.facts_by_shape.get(title_shape)


class InMemoryReferenceCacheStore:
    """reference_cache 的内存 fake（CachedReference 的持久化窄协议）。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ReferenceCache] = {}

    async def find_reference_cache(
        self, title_shape: str, provider: str
    ) -> ReferenceCache | None:
        return self.rows.get((title_shape, provider))

    async def add_reference_cache(self, row: ReferenceCache) -> None:
        self.rows[(row.title_shape, row.provider)] = row


# --- helpers -----------------------------------------------------------------


def _raw() -> RawName:
    return RawName(name=RAW_NAME)


def _l1() -> ParseResult:
    """L1 MEDIUM romaji draft: season missing, episode 3, fansub missing."""
    return ParseResult(
        title=ROMAJI,
        season=None,
        episode=3,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=("season",),
        evidence={
            "title": "name",
            "season": "none",
            "episode": "name",
            "segment": "name",
            "fansub": "none",
        },
    )


def _canonical_series_row(**overrides: Any) -> FakeMemoryRow:
    """A series-level ACTIVE row stored under the canonical title's level-1 key."""
    defaults: dict[str, Any] = {
        "key_level": KEY_LEVEL_SERIES,
        "key_hash": key_hash(level1_key(CANONICAL)),
        "result": {
            "title": CANONICAL,
            "season": 2,
            "episode": None,
            "segment": "episode",
            "fansub": "MSubs",
        },
    }
    defaults.update(overrides)
    return FakeMemoryRow(**defaults)


def _canonical_facts() -> ReferenceFacts:
    return ReferenceFacts(canonical_title=CANONICAL, source="fake")


def _chain(provider: Any, *, enabled: bool = True) -> ReferenceChain:
    registry = Registry()
    registry.register(MetadataReference, "fake")(provider)
    return ReferenceChain(registry, order=("fake",), enabled=enabled)


def _wired_orchestrator(
    recognizer: ParseResult | None,
    *,
    store: FakeMemoryStore,
    chain: ReferenceChain | None = None,
    transport: ScriptedTransport | None = None,
    cache: MemoryCacheStore | None = None,
) -> Orchestrator:
    from autoanime.pipeline.l3_llm import LlmFallbackRecognizer

    return Orchestrator(
        FakeRecognizer(recognizer),
        memory_store=store,
        reference_chain=chain,
        l3_enabled=transport is not None,
        l3_recognizer=LlmFallbackRecognizer(model="test-model") if transport else None,
        llm_transport=transport,
        llm_cache_store=cache,
    )


# --- hit path ----------------------------------------------------------------


async def test_canonical_requery_hit_routes_memory_with_memory_evidence() -> None:
    # L2 misses the romaji keys; the reference chain reports the canonical
    # (Japanese) title; the canonical level-1 key hits the seeded row.
    store = FakeMemoryStore(_canonical_series_row())
    provider = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})
    transport = ScriptedTransport()  # any LLM call would fail the script

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert outcome.degraded is False
    assert outcome.result is not None
    assert outcome.result.season == 2
    assert outcome.result.fansub == "MSubs"
    assert outcome.result.evidence["season"] == "memory"
    assert outcome.result.evidence["key_level"] == "memory:1"
    assert len(store.recorded_hits) == 1
    # The disambiguation query used the L1 draft's title shape, and L3 was
    # never reached as a fallback (no transport call, l3 disabled here).
    assert provider.lookups == [build_title_shape(ROMAJI)]
    assert transport.calls == []


async def test_canonical_requery_hit_on_exact_level() -> None:
    # Canonical level-1 misses; the exact level (canonical shape + the
    # season/episode/fansub parsed from the L1 draft) hits.
    row = FakeMemoryRow(
        key_level=KEY_LEVEL_EXACT,
        key_hash=key_hash(level2_key(CANONICAL, None, 3, None)),
        result={"title": CANONICAL, "season": 2, "episode": 3, "segment": "episode"},
    )
    store = FakeMemoryStore(row)
    provider = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert outcome.result is not None
    assert outcome.result.season == 2
    assert outcome.result.evidence["key_level"] == "memory:2"
    assert len(store.recorded_hits) == 1


async def test_canonical_hit_with_l3_wired_matches_direct_l2_hit_semantics() -> None:
    # With L3 wired, a canonical memory hit behaves exactly like a direct L2
    # hit: the arbiter still runs over the fused result and the L3 draft.
    store = FakeMemoryStore(_canonical_series_row())
    provider = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})
    transport = ScriptedTransport([VALID_RESPONSE])

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
        transport=transport, cache=MemoryCacheStore(),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert outcome.degraded is False
    assert outcome.result is not None
    assert outcome.result.evidence["season"] == "memory"
    assert len(store.recorded_hits) == 1


# --- degradation matrix: every failure silently follows the original path -----


async def test_canonical_requery_miss_falls_through_to_l3() -> None:
    # The chain reports a canonical title but no memory row exists under it.
    store = FakeMemoryStore()
    provider = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})
    transport = ScriptedTransport([VALID_RESPONSE])

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
        transport=transport, cache=MemoryCacheStore(),
    ).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.l2_applied is False
    assert outcome.result is not None
    assert outcome.result.title == ROMAJI
    assert store.recorded_hits == []
    assert len(transport.calls) == 1  # the L3 segment ran as before


async def test_reference_chain_miss_falls_through_to_l3() -> None:
    # Chain lookup returns None (provider miss / negative cache).
    store = FakeMemoryStore(_canonical_series_row())
    provider = FakeReferenceProvider()  # empty map: always a miss

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.l2_applied is False
    assert outcome.result is not None
    assert outcome.result.title == ROMAJI
    assert store.recorded_hits == []
    assert provider.lookups == [build_title_shape(ROMAJI)]


async def test_reference_disabled_falls_through_without_calling_providers() -> None:
    # reference_enabled=False: the chain is closed and reports a miss without
    # consulting any provider.
    store = FakeMemoryStore(_canonical_series_row())
    provider = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider, enabled=False),
    ).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.l2_applied is False
    assert provider.lookups == []


async def test_missing_reference_chain_falls_through_to_l3() -> None:
    # No chain injected: the segment is inert (zero behavior difference).
    store = FakeMemoryStore(_canonical_series_row())

    outcome = await _wired_orchestrator(_l1(), store=store).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.l2_applied is False
    assert outcome.result is not None
    assert outcome.result.title == ROMAJI
    assert store.recorded_hits == []


async def test_empty_canonical_title_falls_through_to_l3() -> None:
    for facts in (
        ReferenceFacts(canonical_title="", source="fake"),
        ReferenceFacts(canonical_title=None, source="fake"),
    ):
        store = FakeMemoryStore(_canonical_series_row())
        provider = FakeReferenceProvider({build_title_shape(ROMAJI): facts})

        outcome = await _wired_orchestrator(
            _l1(), store=store, chain=_chain(provider),
        ).process(_raw())

        assert outcome.route == ROUTE_L3
        assert outcome.l2_applied is False
        assert store.recorded_hits == []


async def test_reference_lookup_error_falls_through_silently() -> None:
    # A failing chain lookup never breaks the parse pass.
    store = FakeMemoryStore(_canonical_series_row())
    provider = FakeReferenceProvider(error=RuntimeError("provider down"))

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.l2_applied is False
    assert outcome.result is not None
    assert outcome.result.title == ROMAJI
    assert store.recorded_hits == []


async def test_canonical_requery_store_error_falls_through_silently() -> None:
    # The L1-key lookups miss (2 calls); the canonical re-lookup raises.
    store = FakeMemoryStore(_canonical_series_row(), fail_after=2)
    provider = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.l2_applied is False
    assert outcome.result is not None
    assert outcome.result.title == ROMAJI
    assert store.recorded_hits == []


# --- cache sharing -------------------------------------------------------------


async def test_disambiguation_and_arbiter_share_reference_cache() -> None:
    # The pre-L3 disambiguation query and the arbiter's reference lookup use
    # the same reference_cache entry: the upstream provider is called at most
    # once even though the chain is queried twice for the same shape.
    upstream = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})
    cached = CachedReference(provider="fake", upstream=upstream,
                             store=InMemoryReferenceCacheStore())
    # trust 0.5: the hit supplements evidence without level fusion, so the
    # arbiter's reference lookup still runs (the fused result stays MEDIUM).
    store = FakeMemoryStore(_canonical_series_row(hit_count=1, corrected_count=1))

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(cached),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert len(upstream.lookups) == 1


# --- alias read-side (PR7 M2b): the alias table is the first lookup link -------


async def test_alias_hit_requery_hit_routes_memory_without_reference_call() -> None:
    # The alias table maps the L1 draft's shape to the canonical shape; the
    # canonical level-1 key hits. The reference chain is never consulted
    # (zero network), and the semantics match a direct L2 hit.
    store = FakeMemoryStore(
        _canonical_series_row(),
        alias_map={build_title_shape(ROMAJI): build_title_shape(CANONICAL)},
    )
    provider = FakeReferenceProvider()  # any chain lookup would be a miss

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert outcome.degraded is False
    assert outcome.result is not None
    assert outcome.result.season == 2
    assert outcome.result.fansub == "MSubs"
    assert outcome.result.evidence["season"] == "memory"
    assert outcome.result.evidence["key_level"] == "memory:1"
    assert len(store.recorded_hits) == 1
    assert store.alias_lookups == [build_title_shape(ROMAJI)]
    assert provider.lookups == []


async def test_alias_hit_requery_miss_falls_through_to_reference_chain() -> None:
    # The alias hit's canonical shape has no memory row: the pass continues
    # down the reference chain link, whose canonical title does hit.
    other_shape = build_title_shape("名脈役割の別作品")
    store = FakeMemoryStore(
        _canonical_series_row(),
        alias_map={build_title_shape(ROMAJI): other_shape},
    )
    provider = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert outcome.result is not None
    assert outcome.result.season == 2
    assert len(store.recorded_hits) == 1
    assert provider.lookups == [build_title_shape(ROMAJI)]


async def test_alias_miss_keeps_m2_reference_chain_behavior() -> None:
    # Regression lock: an alias miss behaves exactly like PR7 M2 -- the same
    # chain lookup runs and its canonical re-query adopts the hit.
    store = FakeMemoryStore(_canonical_series_row())  # alias map empty
    provider = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert outcome.result is not None
    assert outcome.result.evidence["season"] == "memory"
    assert len(store.recorded_hits) == 1
    assert store.alias_lookups == [build_title_shape(ROMAJI)]
    assert provider.lookups == [build_title_shape(ROMAJI)]


async def test_alias_store_error_falls_through_to_reference_chain_silently() -> None:
    # A failing alias read never breaks the pass: the chain link still runs.
    store = FakeMemoryStore(
        _canonical_series_row(),
        alias_error=RuntimeError("alias table unavailable"),
    )
    provider = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert len(store.recorded_hits) == 1


async def test_store_without_alias_extension_falls_through_to_reference_chain() -> None:
    # ``find_alias_key`` is duck-typed off the store: a PR4-era store without
    # the PR7 M3 extension simply skips the alias link.
    class BareMemoryStore(FakeMemoryStore):
        find_alias_key = None  # type: ignore[assignment]

    store = BareMemoryStore(_canonical_series_row())
    provider = FakeReferenceProvider({build_title_shape(ROMAJI): _canonical_facts()})

    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert len(store.recorded_hits) == 1


async def test_alias_hit_with_non_stable_shape_falls_through_to_reference_chain() -> None:
    # Known build_title_shape non-idempotency edge (digit-adjacent separators
    # survive the first fold, then fold once the anchor digits are replaced).
    # When the alias table stores such a non-stable canonical shape, the alias
    # link's re-query re-derives a different key and silently misses; the
    # reference chain -- whose canonical title is the real title -- still
    # rescues the hit. Degradation only costs the chain call, never
    # correctness.
    canonical_title = "Show.Name.S02E05.720p"
    l1_title = "Show Name S02E05"
    alias_shape = build_title_shape(l1_title)  # no digit-adjacent separator
    canonical_shape = build_title_shape(canonical_title)  # non-stable shape
    row = FakeMemoryRow(
        key_level=KEY_LEVEL_EXACT,
        key_hash=key_hash(level2_key(canonical_title, None, 5, None)),
        result={"title": "Show Name S02E05.720p", "season": 1, "episode": 5,
                "segment": "episode"},
    )
    store = FakeMemoryStore(row, alias_map={alias_shape: canonical_shape})
    provider = FakeReferenceProvider(
        {alias_shape: ReferenceFacts(canonical_title=canonical_title, source="fake")}
    )
    l1 = replace(
        _l1(), title=l1_title, season=None, episode=5, missing_fields=("season",)
    )

    outcome = await _wired_orchestrator(
        l1, store=store, chain=_chain(provider),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert outcome.result is not None
    assert outcome.result.episode == 5
    assert len(store.recorded_hits) == 1
    assert provider.lookups == [alias_shape]


# --- build_title_shape idempotency (the alias link's key assumption) -----------


@pytest.mark.parametrize(
    "title",
    [
        "Kono Subarashii Sekai ni Shukufuku wo",
        "Frieren: Beyond Journey   Season  2",  # mixed case + multi-space
        "[Sub] Some Show - 03 (1080p)",
        "第 2 季 某某物语 第 12 话",
        "Mixed CASE  Title with   Spaces",
        "season {season} episode {ep}",  # an already-shaped string
        "一燈  -  EP10",
    ],
)
def test_build_title_shape_is_idempotent(title: str) -> None:
    # The alias link re-uses a canonical *shape* as the draft title, so
    # re-shaping it (inside lookup_memory's key derivation) must be a no-op.
    assert build_title_shape(build_title_shape(title)) == build_title_shape(title)


def test_build_title_shape_known_non_idempotent_edge_is_documented() -> None:
    # Decimal-style separators adjacent to digits survive the first fold (the
    # folding keeps decimals intact); once the anchor digits are replaced the
    # separator becomes foldable. Only release-name-like titles with digit
    # neighbors hit this; the alias link degrades through the reference chain
    # (see test_alias_hit_with_non_stable_shape_falls_through_to_reference_chain).
    title = "Show.Name.S02E05.720p"
    assert build_title_shape(build_title_shape(title)) != build_title_shape(title)


# --- A1'（拍板）：manual alias 命中时确认名覆盖 L1 title ------------------------


async def test_manual_alias_hit_overrides_title_with_confirmed_name() -> None:
    """manual alias 行（用户 confirm 写下的草稿形状映射）命中 → title 用
    确认名原文（记忆行 result.title），evidence 记 memory。回放用户已
    确认的事实，不是猜测。"""
    store = FakeMemoryStore(
        _canonical_series_row(),
        alias_rows={build_title_shape(ROMAJI): (build_title_shape(CANONICAL), "manual")},
    )
    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(FakeReferenceProvider()),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.result is not None
    assert outcome.result.title == CANONICAL
    # evidence=confirmed（高于 name）：L3 段参与的完整仲裁中 title 不被打回
    assert outcome.result.evidence["title"] == "confirmed"


async def test_reference_backfilled_alias_hit_keeps_l1_title() -> None:
    """参考源回填的 alias 行（source=bangumi 等）命中 → 维持补缺语义，
    title 保留 L1 草稿名——参考源数据不覆盖确定性解析。"""
    store = FakeMemoryStore(
        _canonical_series_row(),
        alias_rows={build_title_shape(ROMAJI): (build_title_shape(CANONICAL), "bangumi")},
    )
    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(FakeReferenceProvider()),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.result is not None
    l1 = _l1()
    assert outcome.result.title == l1.title  # L1 name 证据优先（PR4 契约）


async def test_narrow_alias_store_without_source_never_overrides() -> None:
    """只带 find_alias_key 的窄口径 store（PR4 fake / 旧装配）→ 不覆盖，
    行为与 PR7 M2b 完全一致（向后兼容）。"""
    store = FakeMemoryStore(
        _canonical_series_row(),
        alias_map={build_title_shape(ROMAJI): build_title_shape(CANONICAL)},
    )
    store.find_alias_row = None  # type: ignore[reportAttributeAccessIssue]  # 模拟窄口径实现
    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(FakeReferenceProvider()),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.result is not None
    assert outcome.result.title == _l1().title


async def test_confirmed_title_survives_full_arbitration_with_l3() -> None:
    """L3 段参与的完整流程中，confirmed 证据高于 L1 name——title 保持确认名。

    回归（第 6 轮真实测试发现）：memory 命中后 L3 兜底增强，arbiter 逐字段
    仲裁按 evidence_rank 重选——旧代码覆盖 evidence=memory（rank 低于 name）
    被打回 L1 草稿名。
    """
    store = FakeMemoryStore(
        _canonical_series_row(),
        alias_rows={build_title_shape(ROMAJI): (build_title_shape(CANONICAL), "manual")},
    )
    # L3 有线且返回合法响应（title 与 L1 一致——LLM 认可 L1 名也不该翻案）
    outcome = await _wired_orchestrator(
        _l1(), store=store, chain=_chain(FakeReferenceProvider()),
        transport=ScriptedTransport([VALID_RESPONSE]),
        cache=MemoryCacheStore(),
    ).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.result is not None
    assert outcome.result.title == CANONICAL
    assert outcome.result.evidence["title"] == "confirmed"
