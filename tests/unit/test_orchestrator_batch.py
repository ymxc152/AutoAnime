"""E1 orchestrator 批量入口（process_batch）的单元测试（9.3b 接线契约）。

决策契约（事前定死）：

- ``batching=False``（订阅入口）：逐项走 ``process()`` 单文件快路径，行为
  与循环调用逐字节一致——订阅场景永不凑批；
- ``batching=True``（库存入口）：L1/L2 段逐项独立执行（与单文件同一语义，
  含 bypass/前置消歧/降级），HIGH 直接 archive；进入 L3 段的候选项按
  「同目录+同字幕组」经 ``organize_batches`` 划分：
  * 达到阈值的组 → ``enhance_batch`` 一次 LLM 调用（批内每项
    ``batch_applied=True``）；
  * 未达阈值的项 / 缺 folder 或 fansub 的项 → 单文件 L3 快路径；
- PR5 契约保持：L2 命中项的 L3 段照常运行（arbiter 三方输入）；
- L3 不可用/关闭的降级语义与单文件一致；
- 输出列表与输入 ``raws`` 顺序逐位对齐。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import (
    ParseContext,
    ParseResult,
    RawName,
)
from autoanime.pipeline.l3.cache_key import LlmCache
from autoanime.pipeline.l3.prompt import batch_release_names_from_prompt
from autoanime.pipeline.l3_llm import LlmFallbackRecognizer
from autoanime.pipeline.orchestrator import (
    ROUTE_ARCHIVE,
    ROUTE_L3,
    ROUTE_MEMORY,
    Orchestrator,
)

_SINGLE_NAME_RE = re.compile(r"^Release name: (.+)$", re.MULTILINE)


# --- fakes -------------------------------------------------------------------


@dataclass
class ScriptedRecognizer:
    """L1 stand-in：按 raw.name 返回预设结果，缺省返回 ``default``。"""

    by_name: dict[str, ParseResult | None] = field(default_factory=dict)
    default: ParseResult | None = None
    calls: list[str] = field(default_factory=list)

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        self.calls.append(raw.name)
        if raw.name in self.by_name:
            return self.by_name[raw.name]
        return self.default


@dataclass
class FakeMemoryRow:
    key_level: int
    key_hash: str
    result: dict[str, object] = field(default_factory=dict)
    hit_count: int = 0
    corrected_count: int = 0
    status: str = "active"
    title_shape: str | None = None


@dataclass
class FakeMemoryStore:
    rows: dict[tuple[int, str], FakeMemoryRow] = field(default_factory=dict)
    bypassed: frozenset[str] = frozenset()
    recorded_hits: list[Any] = field(default_factory=list)

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None:
        return self.rows.get((key_level, key_hash))

    async def record_hit(
        self, parse_memory: Any, *, operation_id: str | None = None
    ) -> None:
        self.recorded_hits.append(parse_memory)

    async def record_correction(self, parse_memory: Any) -> None:
        return None

    async def has_bypass(self, pattern_hash: str) -> bool:
        return pattern_hash in self.bypassed


@dataclass
class BatchAwareTransport:
    """批量 prompt 回放数组响应；单文件 prompt 按名字映射回放。"""

    batch_response: str | Exception = ""
    single_responses: dict[str, str] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str:
        self.calls.append(prompt)
        if batch_release_names_from_prompt(prompt):
            if isinstance(self.batch_response, Exception):
                raise self.batch_response
            return self.batch_response
        match = _SINGLE_NAME_RE.search(prompt)
        name = match.group(1) if match else ""
        if name not in self.single_responses:
            raise AssertionError(f"unexpected single-file call for {name!r}")
        return self.single_responses[name]


@dataclass
class MemoryCacheStore:
    entries: dict[str, LlmCache] = field(default_factory=dict)

    async def get(self, pattern_hash: str) -> LlmCache | None:
        return self.entries.get(pattern_hash)

    async def put(self, cache: LlmCache) -> None:
        self.entries[cache.pattern_hash] = cache


# --- helpers -----------------------------------------------------------------

MODEL = "fake-model"


def _medium(
    name: str, *, fansub: str | None = None, title: str | None = None
) -> ParseResult:
    """L1 MEDIUM 形态：season 缺失（进 L2/L3 段）。"""
    return ParseResult(
        title=title or name.split(" - ")[0],
        season=None,
        episode=int(name.split(" - ")[-1]),
        segment=Segment.EPISODE,
        fansub=fansub,
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=("season",),
        evidence={"title": "name", "episode": "name"},
    )


def _high(name: str, *, fansub: str | None = None) -> ParseResult:
    return ParseResult(
        title=name.split(" - ")[0],
        season=1,
        episode=int(name.split(" - ")[-1]),
        segment=Segment.EPISODE,
        fansub=fansub,
        level=Confidence.HIGH,
        confidence=0.9,
        missing_fields=(),
        evidence={"title": "name", "season": "name", "episode": "name"},
    )


def _batch_response_for(names: Sequence[str], *, skip: set[int] | None = None) -> str:
    """整批候选的确定性数组响应；``skip`` 里的 index 从响应中剔除。"""
    return json.dumps(
        [
            {
                "index": index,
                "title": name.split(" - ")[0],
                "season": 1,
                "episode": index + 1,
                "segment": "episode",
                "fansub": "SubA",
            }
            for index, name in enumerate(names)
            if skip is None or index not in skip
        ],
        ensure_ascii=False,
    )


def _single_response(name: str) -> str:
    return json.dumps(
        {
            "title": name.split(" - ")[0],
            "season": 1,
            "episode": int(name.split(" - ")[-1]),
            "segment": "episode",
            "fansub": None,
        },
        ensure_ascii=False,
    )


def _recognizer_for(names: Sequence[str], *, fansub: str | None = "SubA") -> ScriptedRecognizer:
    return ScriptedRecognizer(
        by_name={
            name: _medium(name, fansub=fansub) for name in names
        }
    )


def _batch_orchestrator(
    recognizer: ScriptedRecognizer,
    transport: BatchAwareTransport,
    *,
    store: FakeMemoryStore | None = None,
    l3_enabled: bool = True,
) -> Orchestrator:
    return Orchestrator(
        recognizer,
        memory_store=store if store is not None else FakeMemoryStore(),
        l2_enabled=True,
        l3_enabled=l3_enabled,
        # 真 LlmFallbackRecognizer + fake transport：与 test_orchestrator_l3 同一模式，
        # 批量/单文件都走真 schema 解析，仅网络被替换。
        l3_recognizer=LlmFallbackRecognizer(enabled=l3_enabled, model=MODEL)
        if l3_enabled
        else None,
        llm_transport=transport if l3_enabled else None,
        llm_cache_store=MemoryCacheStore() if l3_enabled else None,
    )


# --- tests -------------------------------------------------------------------


async def test_batching_false_is_pure_single_file_loop() -> None:
    names = [f"Show A - {i:02d}" for i in range(1, 6)]
    recognizer = _recognizer_for(names)
    transport = BatchAwareTransport(
        single_responses={name: _single_response(name) for name in names}
    )
    orchestrator = _batch_orchestrator(recognizer, transport)
    raws = [RawName(name=name) for name in names]  # 无 folder：订阅形态

    outcomes = await orchestrator.process_batch(raws, batching=False)

    # 单文件快路径：每项各自一次 LLM 调用，永不凑批。
    assert len(transport.calls) == 5
    assert all(batch_release_names_from_prompt(p) == [] for p in transport.calls)
    assert all(o.route == ROUTE_L3 for o in outcomes)
    assert all(not o.batch_applied for o in outcomes)


async def test_batching_true_packs_same_folder_same_fansub() -> None:
    names = [f"Show A - {i:02d}" for i in range(1, 6)]
    recognizer = _recognizer_for(names, fansub="SubA")
    transport = BatchAwareTransport(batch_response=_batch_response_for(names))
    orchestrator = _batch_orchestrator(recognizer, transport)
    raws = [RawName(name=name, folder="dir1") for name in names]

    outcomes = await orchestrator.process_batch(raws, batching=True)

    # 一次批量调用；输出与输入顺序逐位对齐。
    assert len(transport.calls) == 1
    assert batch_release_names_from_prompt(transport.calls[0]) == names
    assert [o.route for o in outcomes] == [ROUTE_L3] * 5
    assert all(o.batch_applied for o in outcomes)
    assert outcomes[2].result is not None
    assert outcomes[2].result.episode == 3


async def test_batch_never_waits_single_file_unbatchable_items() -> None:
    # 3 个同组 + 2 个无 folder：不足阈值的组与无资格项都走单文件快路径。
    group = [f"Show A - {i:02d}" for i in range(1, 4)]
    solo = ["Solo B - 01", "Solo C - 01"]
    recognizer = ScriptedRecognizer(
        by_name={
            **{name: _medium(name, fansub="SubA") for name in group},
            **{name: _medium(name, fansub="SubA") for name in solo},
        }
    )
    transport = BatchAwareTransport(
        single_responses={name: _single_response(name) for name in group + solo}
    )
    orchestrator = _batch_orchestrator(recognizer, transport)
    raws = [RawName(name=n, folder="dir") for n in group] + [RawName(name=n) for n in solo]

    outcomes = await orchestrator.process_batch(raws, batching=True)

    assert len(transport.calls) == 5  # 全部单文件
    assert all(not o.batch_applied for o in outcomes)


async def test_high_items_skip_l3_and_batch() -> None:
    # 6 个 MEDIUM 凑批 + 1 个 HIGH：HIGH 直接 archive，不进批量 L3 候选。
    names = [f"Show A - {i:02d}" for i in range(1, 7)] + ["Show B - 01"]
    recognizer = ScriptedRecognizer(
        by_name={**{n: _medium(n, fansub="SubA") for n in names[:6]},
                 "Show B - 01": _high("Show B - 01", fansub="SubA")}
    )
    transport = BatchAwareTransport(batch_response=_batch_response_for(names[:6]))
    orchestrator = _batch_orchestrator(recognizer, transport)
    raws = [RawName(name=n, folder="dir") for n in names]

    outcomes = await orchestrator.process_batch(raws, batching=True)

    expected = [ROUTE_L3] * 6 + [ROUTE_ARCHIVE]
    assert [o.route for o in outcomes] == expected
    assert all(not o.batch_applied for o in outcomes[6:])
    # HIGH 项不出现在批量 prompt 的 L3 候选里（6 个候选一批）。
    assert batch_release_names_from_prompt(transport.calls[0]) == names[:6]


async def test_l2_hit_still_runs_l3_batch_and_keeps_memory_route() -> None:
    from autoanime.pipeline.l2 import KEY_LEVEL_SERIES, key_hash, level1_key

    names = [f"Show A - {i:02d}" for i in range(1, 6)]
    recognizer = _recognizer_for(names, fansub="SubA")
    transport = BatchAwareTransport(batch_response=_batch_response_for(names))
    row = FakeMemoryRow(
        key_level=KEY_LEVEL_SERIES,
        key_hash=key_hash(level1_key("Show A")),
        result={
            "title": "Show A",
            "season": 2,
            "episode": None,
            "segment": "episode",
            "fansub": "SubA",
        },
    )
    store = FakeMemoryStore(rows={(KEY_LEVEL_SERIES, key_hash(level1_key("Show A"))): row})
    orchestrator = _batch_orchestrator(recognizer, transport, store=store)
    raws = [RawName(name=n, folder="dir") for n in names]

    outcomes = await orchestrator.process_batch(raws, batching=True)

    # PR5 契约：memory 命中项的 L3 段照常（批量入口同一契约），路由 memory。
    assert all(o.route == ROUTE_MEMORY for o in outcomes)
    assert all(o.l2_applied for o in outcomes)
    assert all(o.batch_applied for o in outcomes)


async def test_batch_item_failing_l3_isolated() -> None:
    # 批量响应缺 index 2：该项走单文件重试（不连坐），其余批量采纳。
    names = [f"Show A - {i:02d}" for i in range(1, 6)]
    recognizer = _recognizer_for(names, fansub="SubA")
    transport = BatchAwareTransport(
        batch_response=_batch_response_for(names, skip={2}),
        single_responses={names[2]: _single_response(names[2])},
    )
    orchestrator = _batch_orchestrator(recognizer, transport)
    raws = [RawName(name=n, folder="dir") for n in names]

    outcomes = await orchestrator.process_batch(raws, batching=True)

    assert len(transport.calls) == 2  # 1 批量 + 1 单文件重试
    assert all(o.route == ROUTE_L3 for o in outcomes)
    assert all(o.result is not None for o in outcomes)
    assert outcomes[2].result is not None
    assert outcomes[2].result.episode == 3
    # batch_applied 标记「经过批量 L3 入口」，失败重试项仍算。
    assert outcomes[2].batch_applied is True


async def test_l3_disabled_batching_keeps_l1_results() -> None:
    names = [f"Show A - {i:02d}" for i in range(1, 6)]
    recognizer = _recognizer_for(names, fansub="SubA")
    transport = BatchAwareTransport()
    orchestrator = _batch_orchestrator(recognizer, transport, l3_enabled=False)
    raws = [RawName(name=n, folder="dir") for n in names]

    outcomes = await orchestrator.process_batch(raws, batching=True)

    assert transport.calls == []
    assert all(o.route == ROUTE_L3 for o in outcomes)
    assert all(o.result is not None and o.result.season is None for o in outcomes)
    assert all(not o.degraded for o in outcomes)


async def test_empty_input_returns_empty_list() -> None:
    orchestrator = _batch_orchestrator(
        ScriptedRecognizer(), BatchAwareTransport()
    )
    assert await orchestrator.process_batch([], batching=True) == []
    assert await orchestrator.process_batch([], batching=False) == []


async def test_contexts_are_honored_per_item() -> None:
    names = [f"Show A - {i:02d}" for i in range(1, 6)]
    recognizer = _recognizer_for(names, fansub="SubA")
    transport = BatchAwareTransport(batch_response=_batch_response_for(names))
    orchestrator = _batch_orchestrator(recognizer, transport)
    raws = [RawName(name=n, folder="dir") for n in names]
    contexts = [ParseContext(release_progress=12) for _ in raws]

    outcomes = await orchestrator.process_batch(raws, contexts=contexts, batching=True)

    # 库存入口接受逐项 context；批量 prompt 注入批内共享上下文。
    assert "latest released episode: 12" in transport.calls[0]
    assert len(outcomes) == 5


async def test_folder_context_falls_back_to_parent_path() -> None:
    names = [f"Show A - {i:02d}" for i in range(1, 6)]
    recognizer = _recognizer_for(names, fansub="SubA")
    transport = BatchAwareTransport(batch_response=_batch_response_for(names))
    orchestrator = _batch_orchestrator(recognizer, transport)
    # folder None 但 parent_path 相同：同目录语义按 parent_path 判定。
    raws = [RawName(name=n, parent_path="Z:\\downloads\\dir") for n in names]

    outcomes = await orchestrator.process_batch(raws, batching=True)

    assert len(transport.calls) == 1
    assert all(o.batch_applied for o in outcomes)
