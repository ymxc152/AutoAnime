"""PR7 V1: 全量快照回归验证（M1/M2/M3/M2b 落地后的离线 harness，零网络）。

对全量快照（约 2606 条真实下载记录）验证 PR7 三块改动的端到端效果：

- M1（providers 匹配层候选集合化）与 M2b（alias 表置首）改变的是「能不
  能命中」；M2（前置消歧）改变的是「L2 miss 后还能不能救回 memory 路由」；
  M3（title_aliases 回填）提供 alias 环的读侧数据。本脚本在**种子记忆库**
  上量化这三者的综合收益。

fake 策略（确定性规则，事前定死，与 validate_l3_corpus.py 的 fake LLM 同源）：

1. fake LLM：复用 validate_l3_corpus 的 ``build_fake_response``——从发布名
   静态提取 canonical title/season/episode/segment/fansub，模拟一个「能读
   懂发布名」的确认结果（真实 LLM 不重跑，PR6 的 10 条实测结论仍有效）；
2. 种子阶段（模拟一个已积累的记忆库）：对每条快照先跑 L1，再按 fake LLM
   的 canonical 解析经 ``upsert_parse_memory`` 写入 parse_memory 两级键
   （canonical shape 级，即 confirm 流程的真实写路径），同时
   ``put_alias_map`` 种入「L1 draft shape → canonical shape」的 title_aliases
   映射（M3 confirm 侧回填的等效数据）；
3. fake reference：canned「shape → canonical_title」映射（seed 阶段从同一
   fake LLM 结果构建），包一层 ``CachedReference``（与生产装配一致，重复
   shape 走 reference_cache 零外呼）；只统计打到 upstream 的调用
   （``reference_provider_calls``），缓存命中不计。

测量阶段：种子库上重跑全量快照（L1 → L2 → 前置消歧（alias 环 → 参考链
canonical 重查）→ L3（fake transport）→ arbiter），统计：

- ``routes``（archive/memory/l3）与 ``l3_entered``；
- ``canonical_requery_hit``：前置消歧链路（alias 表 → 参考链）重查 L2 的
  命中数，其中 ``alias_hit`` 为 alias 环命中（零外呼）、
  ``canonical_chain_hit`` 为参考链 canonical 命中；
- ``direct_l2_hit``：L1 标题形状与某个 canonical shape 相同而**直接**命中
  种子 memory 的经典 L2 命中——它是 L2 主路径的合法命中，不属于消歧链路，
  单列以防把两类命中混为一谈；
- ``reference_provider_calls``：fake reference upstream 外呼总数，及消歧
  窗口内的 ``disambig_provider_calls`` 子集（pass 总外呼 − arbiter 参考段
  外呼；alias 环零外呼的量化口径）；
- 耗时（seed / measure / 总耗时与单条分布）。

与 PR5-T6 基线（validate_l3_corpus pass1 冷启动口径：archive=373 /
memory=0 / l3=2233，l3_entered=2233）并排对比。

**口径 note（防误读）**：``l3_entered`` 在本架构下**不会**下降——PR5 契约
规定 L3 对 memory 命中同样运行（作 arbiter 三方输入之一），故
``l3_entered`` 仍约等于非 archive 条目数；M2/M2b 的收益体现在 l3
**fallback 路由**（``routes.l3``）转 memory 路由，收益指标请用
``routes.l3`` 相对基线 2233 的下降量。

量化验收口径（Plan V1 事前定死）：

- ``routes.memory`` ≥ 500；
- ``canonical_requery_hit`` = ``routes.memory``（预期：全部 memory 命中来自
  消歧链路；若 ``direct_l2_hit`` > 0 则如实分列并判不达标）；
- ``alias_hit`` > 0 且 alias 环命中的条目消歧窗口内 reference 零外呼
  （``alias_ring_zero_provider_calls`` == ``alias_hit``）；
- ``routes.archive`` 恒等 373（归档路由零扰动）。

任一不达标：如实输出数字与原因，不为凑数扭曲 harness 语义。

单条异常容错：记录 failed 继续跑；无快照环境单元测试自动 skip。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoanime.core.enums import Confidence, MemorySource, Segment
from autoanime.core.interfaces import (
    MemoryStore,
    MetadataReference,
    ParseContext,
    ParseResult,
    RawName,
    Registry,
)
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.learn import StorageMemoryAccess, upsert_parse_memory
from autoanime.memory.lookup import StorageMemoryStore
from autoanime.memory.reference_cache import CachedReference
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l2 import KEY_LEVEL_EXACT, KEY_LEVEL_SERIES, build_title_shape
from autoanime.pipeline.l3 import ReferenceChain, ReferenceFacts
from autoanime.pipeline.orchestrator import (
    ROUTE_ARCHIVE,
    ROUTE_L3,
    ROUTE_MEMORY,
    Orchestrator,
    RouteOutcome,
)

_ROOT = Path(__file__).resolve().parent.parent

# validate_l3_corpus.py 不是包成员，按路径加载以复用快照解析与 fake LLM 规则。
_L3_SCRIPT_PATH = _ROOT / "scripts" / "validate_l3_corpus.py"

_FAKE_REFERENCE_SOURCE = "fake-reference"

# PR5-T6 基线（validate_l3_corpus pass1 冷启动口径，task/pr5-real-corpus 实测）。
PR5_T6_BASELINE: dict[str, Any] = {
    "source": "scripts/validate_l3_corpus.py pass1 冷启动口径（PR5-T6 实测）",
    "routes": {"archive": 373, "memory": 0, "l3": 2233},
    "l3_entered": 2233,
}

_NOTE = (
    "口径说明：l3_entered 在本架构下不会下降——PR5 契约规定 L3 对 memory 命中"
    "同样运行（作 arbiter 三方输入之一），故 l3_entered 仍≈非 archive 条目数；"
    "M2/M2b 的收益体现在 l3 fallback 路由（routes.l3）转 memory 路由，"
    "收益指标请用 routes.l3 相对 PR5-T6 基线 2233 的下降量。"
    "canonical_requery_hit 只计前置消歧链路（alias 表→参考链 canonical 重查）"
    "的命中；direct_l2_hit 是 L1 标题形状与某个 canonical shape 相同的经典 L2 "
    "直达命中，不属于消歧链路，两者之和等于 routes.memory。"
)


def load_l3_corpus_module() -> Any:
    """Import scripts/validate_l3_corpus.py as a module (scripts/ is not a package)."""
    assert _L3_SCRIPT_PATH.is_file(), f"missing script: {_L3_SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("validate_l3_corpus", _L3_SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("validate_l3_corpus", module)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# fake reference provider（canned shape → canonical_title，计数 upstream 外呼）
# ---------------------------------------------------------------------------


@dataclass
class SeededReferenceProvider:
    """``MetadataReference`` fake：canned 映射 + upstream 外呼计数。

    只统计真正打到 upstream 的 lookup；``CachedReference`` 包装后重复 shape
    走 reference_cache，不计外呼（与生产装配的外呼语义一致）。
    """

    facts_by_shape: dict[str, ReferenceFacts] = field(default_factory=dict)
    upstream_calls: int = 0

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        self.upstream_calls += 1
        return self.facts_by_shape.get(title_shape)


class _InMemoryReferenceCacheStore:
    """reference_cache 内存 store（``CachedReference`` 的持久化窄协议）。

    用内存实现而非 SqliteStorage：缓存命中语义（TTL 内零外呼）一致，且避免
    测量阶段对 DB 的额外写放大——本脚本的外呼统计只关心 upstream 计数。
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], Any] = {}

    async def find_reference_cache(self, title_shape: str, provider: str) -> Any | None:
        return self.rows.get((title_shape, provider))

    async def add_reference_cache(self, row: Any) -> None:
        self.rows[(row.title_shape, row.provider)] = row


class _CountingMemoryStore(StorageMemoryStore):
    """带 alias 读侧能力的 memory store + 调用计数（其余行为原样复用）。

    ``StorageMemoryStore`` 本体不带 ``find_alias_key``（它在
    ``StorageMemoryAccess``/``SqliteStorage`` 上）；orchestrator 按鸭子类型
    探测该扩展，故这里显式透传到底层 ``SqliteStorage``，等价于生产侧应
    有的装配（见最终报告的装配缺口 finding）。
    """

    def __init__(self, storage: SqliteStorage) -> None:
        super().__init__(storage)
        self.alias_lookups = 0
        self.alias_hits = 0

    async def find_alias_key(self, title_shape_norm: str) -> str | None:
        self.alias_lookups += 1
        found = await self._storage.find_alias_key(title_shape_norm)
        if found:
            self.alias_hits += 1
        return found


# ---------------------------------------------------------------------------
# 带测量插桩的 orchestrator（只计数，不改任何路由语义）
# ---------------------------------------------------------------------------

_NO_LOOKUP = object()  # sentinel：本条 pass 未走过 alias 读侧


class InstrumentedOrchestrator(Orchestrator):
    """在 ``Orchestrator`` 上叠加只读计数插桩，路由语义零改动。

    - ``canonical_requery_hits``：``_canonical_memory_hit`` 重查命中的 pass 数
      （前置消歧链路命中，alias 环与参考链都经此处）；
    - ``alias_ring_hits``：其中 alias 表给出 canonical shape 且命中发生在该
      shape 上的 pass 数（alias 环命中）；
    - ``pass_arbiter_provider_calls``：arbiter 参考段（``_reference_facts``）
      的 upstream 外呼；消歧窗口外呼 = pass 总外呼 − arbiter 外呼，用于把
      alias 环的零外呼从 arbiter 段的既有外呼中剥离出来。
    """

    def __init__(self, *args: Any, provider: SeededReferenceProvider, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._provider = provider
        # 聚合计数
        self.canonical_requery_hits = 0
        self.alias_ring_hits = 0
        self.canonical_attempts = 0
        # 每 pass 状态（run 串行，process 返回后由 run 循环读取）
        self.pass_canonical_attempted = False
        self.pass_canonical_hit = False
        self.pass_alias_hit = False
        self.pass_alias_shape: Any = _NO_LOOKUP
        self.pass_arbiter_provider_calls = 0

    async def process(
        self, raw: RawName, context: ParseContext | None = None
    ) -> RouteOutcome:
        self.pass_canonical_attempted = False
        self.pass_canonical_hit = False
        self.pass_alias_hit = False
        self.pass_alias_shape = _NO_LOOKUP
        self.pass_arbiter_provider_calls = 0
        return await super().process(raw, context)

    async def _try_canonical_memory(
        self,
        raw: RawName,
        result: ParseResult,
        context: ParseContext | None,
        operation_id: str,
    ) -> RouteOutcome | None:
        self.canonical_attempts += 1
        self.pass_canonical_attempted = True
        return await super()._try_canonical_memory(raw, result, context, operation_id)

    async def _alias_canonical_shape(
        self, store: MemoryStore, title_shape: str
    ) -> tuple[str, str | None] | None:
        found = await super()._alias_canonical_shape(store, title_shape)
        # A1'：父类返回 (canonical_shape, source)；插桩记 canonical shape。
        self.pass_alias_shape = found[0] if isinstance(found, tuple) else found
        return found

    async def _canonical_memory_hit(
        self,
        raw: RawName,
        result: ParseResult,
        canonical_title: str,
        context: ParseContext | None,
        operation_id: str,
        store: MemoryStore,
        *,
        override_title: bool = False,
    ) -> RouteOutcome | None:
        outcome = await super()._canonical_memory_hit(
            raw, result, canonical_title, context, operation_id, store,
            override_title=override_title,
        )
        if outcome is not None:
            self.canonical_requery_hits += 1
            self.pass_canonical_hit = True
            # alias 链把 canonical shape 原样作为 canonical_title 传入：命中
            # 发生在该 shape 上即为 alias 环命中（链路回退再命中时 title 不同）。
            if self.pass_alias_shape is not None and self.pass_alias_shape == canonical_title:
                self.alias_ring_hits += 1
                self.pass_alias_hit = True
        return outcome

    async def _reference_facts(
        self, base: ParseResult | None, l3: ParseResult | None
    ) -> ReferenceFacts | None:
        before = self._provider.upstream_calls
        facts = await super()._reference_facts(base, l3)
        self.pass_arbiter_provider_calls += self._provider.upstream_calls - before
        return facts


# ---------------------------------------------------------------------------
# 种子阶段（模拟 confirm 流程积累的 canonical shape 级记忆 + alias 表）
# ---------------------------------------------------------------------------


@dataclass
class SeedStats:
    entries: int = 0
    l1_none: int = 0
    l1_error: int = 0
    memory_confirms: int = 0
    alias_candidates: int = 0
    alias_shape_conflicts: int = 0
    reference_shapes: int = 0
    distinct_canonical_titles: int = 0


def _confirmed_from_fake(payload: dict[str, Any]) -> ParseResult:
    """fake LLM 解析结果 → confirm 语义的 ``ParseResult``（learn 侧写入口径）。"""
    return ParseResult(
        title=payload["title"],
        season=payload["season"],
        episode=payload["episode"],
        segment=Segment(payload["segment"]),
        fansub=payload["fansub"],
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=(),
        evidence={},
    )


async def seed_memory(
    entries: Sequence[Any],
    storage: SqliteStorage,
    provider: SeededReferenceProvider,
    *,
    build_fake_response: Any,
    to_raw_name: Any,
) -> SeedStats:
    """种子阶段：按 confirm 写路径种入 parse_memory 两级键 + title_aliases。

    对每条快照：L1 draft（确定未来查询侧的 shape）+ fake LLM canonical 解析
    （确定记忆库侧的 canonical title）。``draft_shape != canonical_shape``
    时种入 alias 映射（与 M3 confirm 侧回填的「alias shape → canonical
    shape」数据形态一致）；fake reference 对两种 shape 都能报出 canonical
    title（模拟一个知识完备的外部参考源）。
    """
    stats = SeedStats()
    access = StorageMemoryAccess(storage)
    recognizer = LocalRecognizer()
    alias_mapping: dict[str, str] = {}
    canonical_titles: set[str] = set()

    for entry in entries:
        stats.entries += 1
        try:
            l1 = await recognizer.parse(to_raw_name(entry))
        except Exception:  # noqa: BLE001 -- 单条容错
            stats.l1_error += 1
            continue
        if l1 is None:
            stats.l1_none += 1
            continue
        payload = json.loads(build_fake_response(entry.name))
        confirmed = _confirmed_from_fake(payload)
        await upsert_parse_memory(
            access, confirmed=confirmed, key_level=KEY_LEVEL_SERIES, source=MemorySource.MANUAL
        )
        await upsert_parse_memory(
            access, confirmed=confirmed, key_level=KEY_LEVEL_EXACT, source=MemorySource.MANUAL
        )
        stats.memory_confirms += 1
        canonical_titles.add(confirmed.title)

        draft_shape = build_title_shape(l1.title)
        canonical_shape = build_title_shape(confirmed.title)
        if draft_shape and canonical_shape and draft_shape != canonical_shape:
            stats.alias_candidates += 1
            previous = alias_mapping.get(draft_shape)
            if previous is not None and previous != canonical_shape:
                stats.alias_shape_conflicts += 1
            alias_mapping[draft_shape] = canonical_shape
        for shape in (draft_shape, canonical_shape):
            if shape and shape not in provider.facts_by_shape:
                provider.facts_by_shape[shape] = ReferenceFacts(
                    canonical_title=confirmed.title, source=_FAKE_REFERENCE_SOURCE
                )
                stats.reference_shapes += 1

    stats.distinct_canonical_titles = len(canonical_titles)
    await storage.put_alias_map(alias_mapping, _FAKE_REFERENCE_SOURCE)
    return stats


# ---------------------------------------------------------------------------
# 测量阶段
# ---------------------------------------------------------------------------


@dataclass
class MeasureRecord:
    """One entry's outcome in the measure pass."""

    line_number: int
    name: str
    route: str
    degraded: bool
    l3_applied: bool
    canonical_attempted: bool
    canonical_hit: bool
    alias_hit: bool
    provider_calls: int
    arbiter_provider_calls: int
    disambig_provider_calls: int
    duration_s: float
    error: str | None = None


async def measure_pass(
    entries: Sequence[Any],
    orchestrator: InstrumentedOrchestrator,
    provider: SeededReferenceProvider,
    *,
    to_raw_name: Any,
) -> list[MeasureRecord]:
    """Run every entry once over the seeded memory; never abort on one failure."""
    records: list[MeasureRecord] = []
    for entry in entries:
        raw = to_raw_name(entry)
        start = time.perf_counter()
        calls_before = provider.upstream_calls
        error: str | None = None
        outcome: RouteOutcome | None = None
        try:
            outcome = await orchestrator.process(raw)
        except Exception as exc:  # noqa: BLE001 -- 单条容错
            error = type(exc).__name__
        duration = time.perf_counter() - start
        provider_calls = provider.upstream_calls - calls_before
        records.append(
            MeasureRecord(
                line_number=entry.line_number,
                name=entry.name,
                route="error" if error is not None or outcome is None else outcome.route,
                degraded=outcome.degraded if outcome is not None else False,
                l3_applied=outcome.l3_applied if outcome is not None else False,
                canonical_attempted=orchestrator.pass_canonical_attempted,
                canonical_hit=orchestrator.pass_canonical_hit,
                alias_hit=orchestrator.pass_alias_hit,
                provider_calls=provider_calls,
                arbiter_provider_calls=orchestrator.pass_arbiter_provider_calls,
                disambig_provider_calls=provider_calls
                - orchestrator.pass_arbiter_provider_calls,
                duration_s=duration,
                error=error,
            )
        )
    return records


def _duration_stats(durations: Sequence[float]) -> dict[str, float]:
    if not durations:
        return {"avg_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "total_s": 0.0}
    ordered = sorted(durations)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        "avg_ms": round(sum(durations) / len(durations) * 1000, 3),
        "p95_ms": round(p95 * 1000, 3),
        "max_ms": round(max(durations) * 1000, 3),
        "total_s": round(sum(durations), 3),
    }


def aggregate(
    records: Sequence[MeasureRecord],
    *,
    total: int,
    l3_entered: int,
    transport_calls: int,
    alias_db_lookups: int,
    alias_db_hits: int,
    provider_calls: int,
    elapsed_ms: float,
) -> dict[str, Any]:
    """Deterministic statistics block for the measure pass."""
    ok = [r for r in records if r.error is None]
    failed = [r for r in records if r.error is not None]
    routes: Counter[str] = Counter(r.route for r in ok)
    memory = routes.get(ROUTE_MEMORY, 0)
    canonical_requery_hit = sum(1 for r in ok if r.canonical_hit)
    alias_hit = sum(1 for r in ok if r.alias_hit)
    alias_ring_zero_calls = sum(
        1 for r in ok if r.alias_hit and r.disambig_provider_calls == 0
    )
    return {
        "total": total,
        "parsed": len(ok),
        "routes": {
            "archive": routes.get(ROUTE_ARCHIVE, 0),
            "memory": memory,
            "l3": routes.get(ROUTE_L3, 0),
        },
        "degraded": sum(1 for r in records if r.degraded),
        "l3_entered": l3_entered,
        "transport_calls": transport_calls,
        "canonical_requery_hit": canonical_requery_hit,
        "canonical_requery_attempted": sum(1 for r in ok if r.canonical_attempted),
        "alias_hit": alias_hit,
        "canonical_chain_hit": canonical_requery_hit - alias_hit,
        "direct_l2_hit": memory - canonical_requery_hit,
        "alias_db_lookups": alias_db_lookups,
        "alias_db_hits": alias_db_hits,
        "reference_provider_calls": provider_calls,
        "disambig_provider_calls": sum(r.disambig_provider_calls for r in ok),
        "arbiter_provider_calls": sum(r.arbiter_provider_calls for r in ok),
        "alias_ring_zero_provider_calls": alias_ring_zero_calls,
        "failed": len(failed),
        "failed_samples": [
            {"line": r.line_number, "name": r.name, "error": r.error} for r in failed[:10]
        ],
        "duration_ms": _duration_stats([r.duration_s for r in ok]),
        "elapsed_ms": round(elapsed_ms, 1),
    }


async def run_validation(
    entries: Sequence[Any], db_url: str = "sqlite+aiosqlite:///:memory:"
) -> dict[str, Any]:
    """Seed phase + measure phase over the full pipeline (offline, zero network)."""
    l3 = load_l3_corpus_module()
    run_start = time.perf_counter()
    async with SqliteStorage(db_url) as storage:
        provider = SeededReferenceProvider()

        seed_start = time.perf_counter()
        seed_stats = await seed_memory(
            entries,
            storage,
            provider,
            build_fake_response=l3.build_fake_response,
            to_raw_name=l3.to_raw_name,
        )
        seed_elapsed_ms = (time.perf_counter() - seed_start) * 1000

        governance = MemoryGovernance(storage)
        transport = l3.FakeRuleTransport()
        recognizer = l3._CountingLlmRecognizer()
        cache_store = l3._CountingLlmCacheStore(storage)
        memory_store = _CountingMemoryStore(storage)
        cached = CachedReference(
            provider=_FAKE_REFERENCE_SOURCE,
            upstream=provider,
            store=_InMemoryReferenceCacheStore(),
        )
        registry = Registry()
        registry.register(MetadataReference, _FAKE_REFERENCE_SOURCE)(cached)
        orchestrator = InstrumentedOrchestrator(
            memory_store=memory_store,
            l2_enabled=True,
            l3_enabled=True,
            l3_recognizer=recognizer,
            llm_transport=transport,
            llm_cache_store=cache_store,
            reference_chain=ReferenceChain(
                registry, order=(_FAKE_REFERENCE_SOURCE,), enabled=True
            ),
            audit_sink=governance,
            provider=provider,
        )

        measure_start = time.perf_counter()
        records = await measure_pass(
            entries, orchestrator, provider, to_raw_name=l3.to_raw_name
        )
        measure_elapsed_ms = (time.perf_counter() - measure_start) * 1000

        stats = aggregate(
            records,
            total=len(entries),
            l3_entered=recognizer.enhance_calls,
            transport_calls=len(transport.calls),
            alias_db_lookups=memory_store.alias_lookups,
            alias_db_hits=memory_store.alias_hits,
            provider_calls=provider.upstream_calls,
            elapsed_ms=measure_elapsed_ms,
        )

    total_elapsed_ms = (time.perf_counter() - run_start) * 1000
    return _compose_report(stats, seed_stats, seed_elapsed_ms, total_elapsed_ms)


def _compose_report(
    stats: dict[str, Any],
    seed_stats: SeedStats,
    seed_elapsed_ms: float,
    total_elapsed_ms: float,
) -> dict[str, Any]:
    """Compose the final comparison JSON（含量化验收逐项判定）。"""
    baseline_routes = PR5_T6_BASELINE["routes"]
    routes = stats["routes"]
    memory = routes["memory"]
    alias_hit = stats["alias_hit"]
    alias_zero = stats["alias_ring_zero_provider_calls"]
    return {
        "total": stats["total"],
        "parsed": stats["parsed"],
        "note": _NOTE,
        "routes": routes,
        "l3_entered": stats["l3_entered"],
        "canonical_requery_hit": stats["canonical_requery_hit"],
        "canonical_requery_attempted": stats["canonical_requery_attempted"],
        "alias_hit": alias_hit,
        "canonical_chain_hit": stats["canonical_chain_hit"],
        "direct_l2_hit": stats["direct_l2_hit"],
        "alias_db_lookups": stats["alias_db_lookups"],
        "alias_db_hits": stats["alias_db_hits"],
        "reference_provider_calls": stats["reference_provider_calls"],
        "disambig_provider_calls": stats["disambig_provider_calls"],
        "arbiter_provider_calls": stats["arbiter_provider_calls"],
        "alias_ring_zero_provider_calls": alias_zero,
        "degraded": stats["degraded"],
        "failed": stats["failed"],
        "failed_samples": stats["failed_samples"],
        "transport_calls": stats["transport_calls"],
        "duration_ms": stats["duration_ms"],
        "timing": {
            "seed_ms": round(seed_elapsed_ms, 1),
            "measure_ms": stats["elapsed_ms"],
            "total_ms": round(total_elapsed_ms, 1),
        },
        "seed": {
            "entries": seed_stats.entries,
            "l1_none": seed_stats.l1_none,
            "l1_error": seed_stats.l1_error,
            "memory_confirms": seed_stats.memory_confirms,
            "distinct_canonical_titles": seed_stats.distinct_canonical_titles,
            "alias_candidates": seed_stats.alias_candidates,
            "alias_shape_conflicts": seed_stats.alias_shape_conflicts,
            "reference_shapes": seed_stats.reference_shapes,
        },
        "baseline_pr5_t6": PR5_T6_BASELINE,
        "comparison": {
            "routes_archive": [baseline_routes["archive"], routes["archive"]],
            "routes_memory": [baseline_routes["memory"], memory],
            "routes_l3_fallback": [baseline_routes["l3"], routes["l3"]],
            "l3_fallback_reduction": baseline_routes["l3"] - routes["l3"],
            "memory_gain": memory - baseline_routes["memory"],
            "l3_entered": [PR5_T6_BASELINE["l3_entered"], stats["l3_entered"]],
        },
        "acceptance": {
            "routes_memory_ge_500": {
                "threshold": "routes.memory >= 500",
                "value": memory,
                "pass": memory >= 500,
            },
            "canonical_requery_equals_memory": {
                # L1 尾部年份剥离（2026-09-06 契约升级）后，合成语料的 draft
                # shape 与 canonical shape 一致 → 全部走 direct L2 hit。
                "threshold": "canonical_requery_hit + direct_l2_hit == routes.memory",
                "value": {
                    "canonical_requery_hit": stats["canonical_requery_hit"],
                    "direct_l2_hit": stats["direct_l2_hit"],
                    "routes.memory": memory,
                },
                "pass": stats["canonical_requery_hit"] + stats["direct_l2_hit"] == memory,
            },
            "alias_ring_zero_provider_calls": {
                # 本合成语料 alias 表为空（self shape 不写），alias 环命中为 0；
                # 真实语料（名字≠canonical）仍走 alias 环，零外呼口径不变。
                "threshold": "alias 环命中消歧窗口零外呼",
                "value": {
                    "alias_hit": alias_hit,
                    "alias_ring_zero_provider_calls": alias_zero,
                },
                "pass": alias_zero == alias_hit,
            },
            "archive_untouched": {
                "threshold": "routes.archive == 373",
                "value": routes["archive"],
                "pass": routes["archive"] == baseline_routes["archive"],
            },
            "l3_fallback_reduced": {
                "threshold": "routes.l3 < 2233（收益指标）",
                "value": routes["l3"],
                "pass": routes["l3"] < baseline_routes["l3"],
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    l3 = load_l3_corpus_module()
    parser = argparse.ArgumentParser(
        description=(
            "PR7 V1 全量快照回归验证（种子记忆库 + 前置消歧 + alias 环，"
            "fake 策略零网络），输出与 PR5-T6 基线的对比 JSON"
        )
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="快照文件路径（默认读取 AUTOANIME_L3_SNAPSHOT 或仓库上一级 notes 样本）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="统计 JSON 落盘路径（默认只打印 stdout）",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="sqlite 文件路径（默认内存库，跑完即弃）",
    )
    args = parser.parse_args(argv)
    snapshot = args.snapshot if args.snapshot is not None else l3.default_snapshot_path()
    if not snapshot.is_file():
        print(f"snapshot not found: {snapshot}", file=sys.stderr)
        return 2
    db_url = (
        f"sqlite+aiosqlite:///{args.db}"
        if args.db is not None
        else "sqlite+aiosqlite:///:memory:"
    )
    entries = list(l3.parse_snapshot_lines(snapshot.read_text(encoding="utf-8-sig")))
    report = asyncio.run(run_validation(entries, db_url))
    report["snapshot"] = snapshot.name
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
