"""E1: 全量快照指标产出（L1→L2→L3(fake)→机会主义合批，离线零网络）。

对全量快照（约 2606 条真实下载记录）按**库存导入入口**跑完整管线
（``Orchestrator.process_batch(batching=True)``，合批阈值取 config 的
``batch_min_size``/``batch_max_size``），并排跑一遍单文件参考口径，产出
M2 指标 JSON（ARCHITECTURE 5.5 / 9.3b 口径）。

复用先例脚本（scripts 不是包，按路径加载，与 validate_pr7_corpus.py 同一
模式）：快照解析/``to_raw_name``/fake LLM 规则/种子策略全部来自
``validate_l3_corpus.py`` 与 ``validate_pr7_corpus.py``，不另起口径。

fake 策略（确定性规则，与先例同源）：

1. fake LLM：``build_fake_response``（validate_l3_corpus 的静态规则）。
   **批量感知 transport**：批量 prompt（``Release name {i}:`` 逐行）按
   index 构造数组响应（每项 = 单文件规则结果 + ``index`` 对齐字段），
   单文件 prompt 走原规则——模拟一个「能读懂发布名」的 LLM 的保守行为，
   整链零网络；
2. 种子阶段（模拟已积累的记忆库，validate_pr7_corpus 的 seed 策略原样
   复用）：每条快照经 ``upsert_parse_memory`` 写两级键 + ``put_alias_map``
   种入「L1 draft shape → canonical shape」映射；
3. fake reference：canned「shape → canonical_title」+ ``CachedReference``
   包装（与生产装配一致）。

两遍测量（两份独立种子库，按确定性种子策略内容逐字节一致，互不污染）：

- **batch pass**（主口径）：``process_batch`` 一次性导入全量快照（9.3b
  典型触发 = 首次库存导入），统计 total/l1_high/l2_hit/l3_entered/
  llm_calls/合批批次统计/canonical_hit/alias_hit；
- **single pass**（参考口径）：逐条 ``process``，提供 per-item 全管线
  时延分布（p50/p95，与 PR5-T6/PR7 脚本同口径）与不合批时的 LLM 调用
  基线（合批节省 = 基线 − batch pass）。

**目录上下文口径（快照是平面列表，目录树已丢失）**：``[D]`` 目录条目
自带 folder=自身（每个目录一个识别单元，天然不凑批）；``[F]`` 散文件
的 folder 由 ``--folder-strategy`` 重建：

- ``title``（默认）：L1 draft title 作「同发布目录」代理——与 qBittorrent
  「一种子一目录」的常见布局对齐，批次内模板化最强（9.3b 的合批收益
  机制）；folder 只影响合批分组，不影响任何识别语义；
- ``root``：全部 [F] 视为同一下载根目录——批键退化为「同字幕组」，
  9.3b 契约同样合法（批内可跨番，批量 prompt 逐条独立解析）。

**口径 note**：``l3_entered`` 不随合批下降——PR5 契约规定 L3 对 memory
命中同样运行；合批的收益体现在 ``llm_calls``（transport 调用次数），
``llm_calls.saved_by_batching`` = single pass 基线 − batch pass。批量
调用失败项的单文件重试计入 ``single_calls``（真实成本），批量调用本身
计 ``batch_calls``（一次调用覆盖整批）。

单条异常容错：single pass 逐条记录 failed 继续跑；batch pass 的管线
内部异常（L1 parse 抛错）会中止整批——harness 捕获后按全批失败如实
记录（process_batch 的逐项容错缺口见最终报告）。
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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from autoanime.config import load_settings
from autoanime.core.interfaces import (
    MetadataReference,
    ParseContext,
    ParseResult,
    RawName,
    Registry,
)
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.reference_cache import CachedReference
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l3 import ReferenceChain
from autoanime.pipeline.l3.prompt import batch_release_names_from_prompt
from autoanime.pipeline.orchestrator import (
    ROUTE_ARCHIVE,
    ROUTE_L3,
    ROUTE_MEMORY,
    Orchestrator,
)

_ROOT = Path(__file__).resolve().parent.parent
_PR7_SCRIPT_PATH = _ROOT / "scripts" / "validate_pr7_corpus.py"


def load_modules() -> tuple[Any, Any]:
    """Load the precedent scripts as modules: (validate_pr7_corpus, validate_l3_corpus).

    scripts 不是包，按路径加载（与 validate_pr7_corpus.py 复用 l3 脚本同一
    模式）：种子策略/记忆 store 适配取自 pr7 模块，快照解析/fake LLM 规则
    取自 l3 模块。
    """
    assert _PR7_SCRIPT_PATH.is_file(), f"missing script: {_PR7_SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("validate_pr7_corpus", _PR7_SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("validate_pr7_corpus", module)
    spec.loader.exec_module(module)
    return module, module.load_l3_corpus_module()


# ---------------------------------------------------------------------------
# 批量感知 fake transport（数组响应对齐批内 index；单文件走原规则）
# ---------------------------------------------------------------------------


class FakeBatchRuleTransport:
    """``LlmTransport`` fake：批量 prompt 回数组响应，单文件 prompt 回单对象。

    与 validate_l3_corpus.FakeRuleTransport 同一确定性规则（只依赖发布名
    本身），只是补上批量形态：``parse_batch_response`` 需要每项带
    ``index``，这里按 prompt 中机械提取的 release name 顺序构造。
    """

    def __init__(self, build_fake_response: Any, raw_name_from_prompt: Any) -> None:
        self._build_single = build_fake_response
        self._name_from_single_prompt = raw_name_from_prompt
        self.calls: list[str] = []

    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str:
        self.calls.append(prompt)
        names = batch_release_names_from_prompt(prompt)
        if names:
            items = [
                {"index": index, **json.loads(self._build_single(name))}
                for index, name in enumerate(names)
            ]
            return json.dumps(items, ensure_ascii=False)
        return self._build_single(self._name_from_single_prompt(prompt))


# ---------------------------------------------------------------------------
# 测量插桩（只计数，不改任何路由/合批语义）
# ---------------------------------------------------------------------------

_NO_LOOKUP = object()  # sentinel：当前消歧尝试未走过 alias 读侧


class _TimingL1Recognizer:
    """L1 识别器计时代理：记录每次 parse 时长与结果等级（只读插桩）。"""

    def __init__(self, inner: LocalRecognizer) -> None:
        self._inner = inner
        self.durations_s: list[float] = []
        self.levels: Counter[str] = Counter()
        self.none_results = 0

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        start = time.perf_counter()
        result = await self._inner.parse(raw, context)
        self.durations_s.append(time.perf_counter() - start)
        if result is None:
            self.none_results += 1
        else:
            self.levels[result.level.value] += 1
        return result


class InstrumentedOrchestrator(Orchestrator):
    """只读计数插桩（validate_pr7_corpus 同款语义，改为跨 process_batch 聚合）。

    - ``canonical_attempts``/``canonical_requery_hits``：前置消歧尝试与
      重查命中（alias 环与参考链都经 ``_canonical_memory_hit``）；
    - ``alias_ring_hits``：其中 alias 表给出 canonical shape 且命中发生在
      该 shape 上（零外呼链路）。

    批量入口逐项串行执行 L1/L2 段，``_alias_canonical_shape`` 与
    ``_canonical_memory_hit`` 在同一次消歧尝试内先后发生，因此用
    「当前尝试」状态即可归因，计数器跨项只增、无需 per-pass 重置。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.canonical_attempts = 0
        self.canonical_requery_hits = 0
        self.alias_ring_hits = 0
        self._attempt_alias_shape: Any = _NO_LOOKUP

    async def _try_canonical_memory(
        self,
        raw: RawName,
        result: ParseResult,
        context: ParseContext | None,
        operation_id: str,
    ) -> Any:
        self.canonical_attempts += 1
        self._attempt_alias_shape = _NO_LOOKUP
        return await super()._try_canonical_memory(raw, result, context, operation_id)

    async def _alias_canonical_shape(self, store: Any, title_shape: str) -> str | None:
        found = await super()._alias_canonical_shape(store, title_shape)
        self._attempt_alias_shape = found
        return found

    async def _canonical_memory_hit(
        self,
        raw: RawName,
        result: ParseResult,
        canonical_title: str,
        context: ParseContext | None,
        operation_id: str,
        store: Any,
    ) -> Any:
        outcome = await super()._canonical_memory_hit(
            raw, result, canonical_title, context, operation_id, store
        )
        if outcome is not None:
            self.canonical_requery_hits += 1
            if (
                self._attempt_alias_shape is not None
                and self._attempt_alias_shape == canonical_title
            ):
                self.alias_ring_hits += 1
        return outcome


# ---------------------------------------------------------------------------
# 目录上下文重建（平面快照 → RawName.folder）
# ---------------------------------------------------------------------------


#: ``--folder-strategy root`` 的合成目录标签：全部 [F] 散文件共享同一下载根。
_ROOT_FOLDER_LABEL = "download-root"


async def build_raws(
    entries: Sequence[Any],
    *,
    to_raw_name: Any,
    folder_strategy: str,
) -> list[RawName]:
    """快照条目 → 带 folder 上下文的 ``RawName`` 列表（输入顺序不变）。

    ``[D]`` 目录条目自带 folder=自身；``[F]`` 散文件按 ``folder_strategy``
    重建（口径见模块 docstring）：``title`` 用 L1 draft title 作「同发布
    目录」代理，``root`` 用合成根标签（批键退化为「同字幕组」）。L1 解析
    失败/无标题的条目 folder 保持 ``None``——永不凑批，走单文件快路径。
    """
    recognizer = LocalRecognizer()
    raws: list[RawName] = []
    for entry in entries:
        raw = to_raw_name(entry)
        if entry.kind == "F" and raw.folder is None:
            if folder_strategy == "root":
                raw = replace(raw, folder=_ROOT_FOLDER_LABEL)
            elif folder_strategy == "title":
                try:
                    draft = await recognizer.parse(raw)
                except Exception:  # noqa: BLE001 -- 单条容错
                    draft = None
                if draft is not None and draft.title:
                    raw = replace(raw, folder=draft.title)
        raws.append(raw)
    return raws


# ---------------------------------------------------------------------------
# 测量统计
# ---------------------------------------------------------------------------


def _split_transport_calls(calls: Sequence[str]) -> dict[str, int]:
    """transport 调用形态拆分：总次数 / 批量次数 / 单文件次数 / 批覆盖项数。"""
    batch_calls = 0
    single_calls = 0
    batched_items = 0
    batch_size_max = 0
    for prompt in calls:
        names = batch_release_names_from_prompt(prompt)
        if names:
            batch_calls += 1
            batched_items += len(names)
            batch_size_max = max(batch_size_max, len(names))
        else:
            single_calls += 1
    return {
        "total": len(calls),
        "batch_calls": batch_calls,
        "single_calls": single_calls,
        "batched_items": batched_items,
        "batch_size_max": batch_size_max,
    }


def _duration_percentiles(durations_s: Sequence[float]) -> dict[str, float]:
    if not durations_s:
        return {"avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "total_s": 0.0}
    ordered = sorted(durations_s)

    def _p(ratio: float) -> float:
        return ordered[max(0, math.ceil(ratio * len(ordered)) - 1)] * 1000

    return {
        "avg_ms": round(sum(durations_s) / len(durations_s) * 1000, 3),
        "p50_ms": round(_p(0.50), 3),
        "p95_ms": round(_p(0.95), 3),
        "max_ms": round(max(durations_s) * 1000, 3),
        "total_s": round(sum(durations_s), 3),
    }


# ---------------------------------------------------------------------------
# 测量阶段
# ---------------------------------------------------------------------------


@dataclass
class RoundResult:
    """One (seed → measure) round on a fresh in-memory store."""

    seed_stats: Any = None
    routes: Counter[str] = field(default_factory=Counter)
    failed: list[dict[str, Any]] = field(default_factory=list)
    degraded: int = 0
    #: single pass：per-item 全管线时延；batch pass 无 per-item 全管线口径。
    durations_s: list[float] = field(default_factory=list)
    #: 两模式共有：L1 parse 逐项时延（batch pass 的 per-item 可测口径）。
    l1_durations_s: list[float] = field(default_factory=list)
    l1_levels: Counter[str] = field(default_factory=Counter)
    l1_none: int = 0
    transport: dict[str, int] = field(default_factory=dict)
    batch_applied_outcomes: int = 0
    canonical_attempts: int = 0
    canonical_requery_hits: int = 0
    alias_ring_hits: int = 0
    wall_s: float = 0.0


async def _run_round(
    pr7: Any,
    l3: Any,
    entries: Sequence[Any],
    *,
    mode: str,
    folder_strategy: str = "title",
    batch_min_size: int = 5,
    batch_max_size: int = 20,
) -> RoundResult:
    """种子 + 一次测量。``mode="batch"`` 走 process_batch，``"single"`` 逐条 process。

    每轮使用全新内存库与全新 provider：两轮种子内容按确定性策略一致，
    而记忆/缓存写入互不污染（llm_calls 基线可比）。
    """
    result = RoundResult()
    async with SqliteStorage("sqlite+aiosqlite:///:memory:") as storage:
        provider = pr7.SeededReferenceProvider()
        result.seed_stats = await pr7.seed_memory(
            entries,
            storage,
            provider,
            build_fake_response=l3.build_fake_response,
            to_raw_name=l3.to_raw_name,
        )
        l1 = _TimingL1Recognizer(LocalRecognizer())
        transport = FakeBatchRuleTransport(l3.build_fake_response, l3.raw_name_from_prompt)
        cache_store = l3._CountingLlmCacheStore(storage)
        memory_store = pr7._CountingMemoryStore(storage)
        cached = CachedReference(
            provider=pr7._FAKE_REFERENCE_SOURCE,
            upstream=provider,
            store=pr7._InMemoryReferenceCacheStore(),
        )
        registry = Registry()
        registry.register(MetadataReference, pr7._FAKE_REFERENCE_SOURCE)(cached)
        orchestrator = InstrumentedOrchestrator(
            recognizer=l1,
            memory_store=memory_store,
            l2_enabled=True,
            l3_enabled=True,
            l3_recognizer=l3._CountingLlmRecognizer(),
            llm_transport=transport,
            llm_cache_store=cache_store,
            reference_chain=ReferenceChain(
                registry, order=(pr7._FAKE_REFERENCE_SOURCE,), enabled=True
            ),
            audit_sink=MemoryGovernance(storage),
        )

        wall_start = time.perf_counter()
        if mode == "batch":
            raws = await build_raws(
                entries, to_raw_name=l3.to_raw_name, folder_strategy=folder_strategy
            )
            try:
                outcomes = await orchestrator.process_batch(
                    raws,
                    batching=True,
                    batch_min_size=batch_min_size,
                    batch_max_size=batch_max_size,
                )
            except Exception as exc:  # noqa: BLE001 -- 整批失败如实记录
                result.failed.append(
                    {"line": None, "name": None, "error": f"batch_pass_aborted:{type(exc).__name__}"}
                )
                result.wall_s = time.perf_counter() - wall_start
                result.transport = _split_transport_calls(transport.calls)
                return result
            for outcome in outcomes:
                result.routes[outcome.route] += 1
                if outcome.degraded:
                    result.degraded += 1
                if outcome.batch_applied:
                    result.batch_applied_outcomes += 1
        else:
            for entry in entries:
                raw = l3.to_raw_name(entry)
                start = time.perf_counter()
                try:
                    outcome = await orchestrator.process(raw)
                except Exception as exc:  # noqa: BLE001 -- 单条容错
                    result.failed.append(
                        {"line": entry.line_number, "name": entry.name, "error": type(exc).__name__}
                    )
                    continue
                result.durations_s.append(time.perf_counter() - start)
                result.routes[outcome.route] += 1
                if outcome.degraded:
                    result.degraded += 1
        result.wall_s = time.perf_counter() - wall_start

        result.l1_durations_s = list(l1.durations_s)
        result.l1_levels = +l1.levels
        result.l1_none = l1.none_results
        result.canonical_attempts = orchestrator.canonical_attempts
        result.canonical_requery_hits = orchestrator.canonical_requery_hits
        result.alias_ring_hits = orchestrator.alias_ring_hits
        result.transport = _split_transport_calls(transport.calls)
    return result


async def run_validation(
    entries: Sequence[Any],
    *,
    folder_strategy: str = "title",
    batch_min_size: int | None = None,
    batch_max_size: int | None = None,
) -> dict[str, Any]:
    """Seed + 两遍测量（batch 主口径 / single 参考口径），全离线零网络。"""
    pr7, l3 = load_modules()
    settings = load_settings()
    min_size = batch_min_size if batch_min_size is not None else settings.batch_min_size
    max_size = batch_max_size if batch_max_size is not None else settings.batch_max_size

    run_start = time.perf_counter()
    batch_round = await _run_round(
        pr7,
        l3,
        entries,
        mode="batch",
        folder_strategy=folder_strategy,
        batch_min_size=min_size,
        batch_max_size=max_size,
    )
    single_round = await _run_round(pr7, l3, entries, mode="single")
    total_elapsed_ms = (time.perf_counter() - run_start) * 1000
    return _compose_report(
        total=len(entries),
        batch_round=batch_round,
        single_round=single_round,
        min_size=min_size,
        max_size=max_size,
        folder_strategy=folder_strategy,
        total_elapsed_ms=total_elapsed_ms,
    )


def _compose_report(
    *,
    total: int,
    batch_round: RoundResult,
    single_round: RoundResult,
    min_size: int,
    max_size: int,
    folder_strategy: str,
    total_elapsed_ms: float,
) -> dict[str, Any]:
    routes = {
        "archive": batch_round.routes.get(ROUTE_ARCHIVE, 0),
        "memory": batch_round.routes.get(ROUTE_MEMORY, 0),
        "l3": batch_round.routes.get(ROUTE_L3, 0),
    }
    l2_hit = routes["memory"]
    l1_high = routes["archive"]  # 批量入口下 archive 路由 ⟺ L1 HIGH 直达归档
    l3_entered = total - routes["archive"] - len(batch_round.failed)
    memory_window = routes["memory"] + routes["l3"]
    canonical_hit = batch_round.canonical_requery_hits
    alias_hit = batch_round.alias_ring_hits
    llm_calls_batch = batch_round.transport.get("total", 0)
    llm_calls_single = single_round.transport.get("total", 0)
    saved = llm_calls_single - llm_calls_batch
    single_latency = _duration_percentiles(single_round.durations_s)
    return {
        "total": total,
        "l1_high": l1_high,
        "l1_levels": {
            "high": batch_round.l1_levels.get("high", 0),
            "medium": batch_round.l1_levels.get("medium", 0),
            "low": batch_round.l1_levels.get("low", 0),
            "none": batch_round.l1_none,
            "source": "batch_pass L1 recognizer 计数（l1_high 应与 routes.archive 一致）",
        },
        "l2_hit": l2_hit,
        "l3_entered": l3_entered,
        "routes": routes,
        "memory_hit_rate": round(l2_hit / memory_window, 4) if memory_window else 0.0,
        "canonical_hit": canonical_hit,
        "alias_hit": alias_hit,
        "canonical_requery_attempted": batch_round.canonical_attempts,
        "canonical_chain_hit": canonical_hit - alias_hit,
        "direct_l2_hit": l2_hit - canonical_hit,
        "llm_calls": {
            "batch_pass": {
                "transport_calls": llm_calls_batch,
                "batch_calls": batch_round.transport.get("batch_calls", 0),
                "single_calls": batch_round.transport.get("single_calls", 0),
                "llm_calls_per_file": round(llm_calls_batch / total, 4) if total else 0.0,
            },
            "single_pass": {
                "transport_calls": llm_calls_single,
                "llm_calls_per_file": round(llm_calls_single / total, 4) if total else 0.0,
            },
            "saved_by_batching": saved,
            "saved_ratio": round(saved / llm_calls_single, 4) if llm_calls_single else 0.0,
        },
        "p50_p95_ms": {
            "p50_ms": single_latency["p50_ms"],
            "p95_ms": single_latency["p95_ms"],
            "scope": "single_pass per-item 全管线（与 PR5-T6/PR7 脚本同口径）",
        },
        "batching": {
            "min_batch_size": min_size,
            "max_batch_size": max_size,
            "batch_calls": batch_round.transport.get("batch_calls", 0),
            "batched_items": batch_round.transport.get("batched_items", 0),
            "avg_batch_size": round(
                batch_round.transport.get("batched_items", 0)
                / batch_round.transport.get("batch_calls", 1),
                2,
            )
            if batch_round.transport.get("batch_calls")
            else 0.0,
            "max_batch_size_observed": batch_round.transport.get("batch_size_max", 0),
            "batch_applied_outcomes": batch_round.batch_applied_outcomes,
            "single_l3_calls_incl_retries": batch_round.transport.get("single_calls", 0),
            "folder_strategy": folder_strategy,
        },
        "degraded": {"batch_pass": batch_round.degraded, "single_pass": single_round.degraded},
        "failed": {"batch_pass": len(batch_round.failed), "single_pass": len(single_round.failed)},
        "failed_samples": (batch_round.failed + single_round.failed)[:10],
        "latency_ms": {
            "single_pass": single_latency,
            "batch_pass": {
                "wall_ms": round(batch_round.wall_s * 1000, 1),
                "per_item_avg_ms": round(batch_round.wall_s * 1000 / total, 3) if total else 0.0,
                "l1_parse": _duration_percentiles(batch_round.l1_durations_s),
            },
        },
        "timing": {
            "batch_pass_wall_ms": round(batch_round.wall_s * 1000, 1),
            "single_pass_wall_ms": round(single_round.wall_s * 1000, 1),
            "total_ms": round(total_elapsed_ms, 1),
        },
        "seed": {
            "entries": batch_round.seed_stats.entries,
            "l1_none": batch_round.seed_stats.l1_none,
            "l1_error": batch_round.seed_stats.l1_error,
            "memory_confirms": batch_round.seed_stats.memory_confirms,
            "distinct_canonical_titles": batch_round.seed_stats.distinct_canonical_titles,
            "alias_candidates": batch_round.seed_stats.alias_candidates,
            "alias_shape_conflicts": batch_round.seed_stats.alias_shape_conflicts,
            "reference_shapes": batch_round.seed_stats.reference_shapes,
        },
        "note": (
            "口径：l3_entered = total - archive - failed（进入 L3 段的条数，含 memory 路由——"
            "PR5 契约 L3 对 memory 命中同样运行）；合批收益看 llm_calls.saved_by_batching。"
            "l1_high = archive 路由数（批量入口下 L1 HIGH 直达归档）。l2_hit = memory 路由数，"
            "= direct_l2_hit + canonical_hit（前置消歧重查命中），其中 alias_hit 为 alias 环命中。"
            "memory_hit_rate 分母 = memory + l3 路由 pass 数。"
            "folder_strategy 重建平面快照丢失的目录上下文（详见脚本 docstring）。"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    pr7, l3 = load_modules()
    parser = argparse.ArgumentParser(
        description=(
            "E1 M2 指标产出：全量快照离线跑 L1→L2→L3(fake)→机会主义合批，"
            "输出指标 JSON（total/l1_high/l2_hit/l3_entered/llm_calls/p50_p95_ms/"
            "记忆命中率/合批批次统计/canonical_hit/alias_hit）"
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
        "--folder-strategy",
        choices=("title", "root"),
        default="title",
        help="[F] 散文件的目录上下文重建策略（默认 title：L1 draft title 作同发布目录代理）",
    )
    args = parser.parse_args(argv)
    snapshot = args.snapshot if args.snapshot is not None else l3.default_snapshot_path()
    if not snapshot.is_file():
        print(f"snapshot not found: {snapshot}", file=sys.stderr)
        return 2
    entries = list(l3.parse_snapshot_lines(snapshot.read_text(encoding="utf-8-sig")))
    report = asyncio.run(run_validation(entries, folder_strategy=args.folder_strategy))
    report["snapshot"] = snapshot.name
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["failed"]["batch_pass"] + report["failed"]["single_pass"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
