"""E1 批量 L3 识别器（LlmFallbackRecognizer.enhance_batch）的单元测试。

契约（9.3b，事前定死）：

- 批内一次真实 transport 调用（数组输出）；``calls_used`` 计 1 次真实
  调用，网络重试不计（与单文件同一口径）；
- 批量响应逐项校验：合法项直接采纳；失败项**单独**走单文件路径重试
  （含 llm_cache 读写与 schema 纠正语义）——不连坐；
- 批量调用本身不写 llm_cache（无稳定单 pattern 键；单文件重试路径各自
  写缓存）；批量响应缓存命中语义不属于 v1 批量契约；
- 整批响应非法（非 JSON/非数组）视为全批失败：全部项走单文件路径；
- 不可用（disabled / model 缺失）：全 ``None``、零调用，与单文件 R7
  语义一致；
- transport 网络类失败按 ``transport_retry_allowed`` 重试（批量与单文件
  各自独立计数）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import (
    LlmCacheStore,
    ParseContext,
    ParseResult,
    RawName,
)
from autoanime.pipeline.l3.cache_key import LlmCache
from autoanime.pipeline.l3.prompt import batch_release_names_from_prompt
from autoanime.pipeline.l3_llm import LlmFallbackRecognizer

_SINGLE_NAME_RE = re.compile(r"^Release name: (.+)$", re.MULTILINE)


def _batch_payload(names: Sequence[str], *, skip: set[int] | None = None) -> str:
    """确定性批量响应：每个 name 的 title=清洗名、episode=序号+1。"""
    items = []
    for index, name in enumerate(names):
        if skip is not None and index in skip:
            continue
        items.append(
            {
                "index": index,
                "title": name.split(" - ")[0],
                "season": 1,
                "episode": index + 1,
                "segment": "episode",
                "fansub": "SubA",
            }
        )
    return json.dumps(items, ensure_ascii=False)


@dataclass
class BatchScriptedTransport:
    """按 prompt 形态回放：批量 prompt 走批脚本，单文件 prompt 走名字映射。"""

    batch_script: list[str | Exception] = field(default_factory=list)
    single_script: dict[str, str] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str:
        self.calls.append(prompt)
        names = batch_release_names_from_prompt(prompt)
        if names:
            item = self.batch_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        match = _SINGLE_NAME_RE.search(prompt)
        name = match.group(1) if match else ""
        if name not in self.single_script:
            raise AssertionError(f"no scripted single response for {name!r}")
        return self.single_script[name]


@dataclass
class MemoryCacheStore:
    entries: dict[str, LlmCache] = field(default_factory=dict)
    puts: int = 0

    async def get(self, pattern_hash: str) -> LlmCache | None:
        return self.entries.get(pattern_hash)

    async def put(self, cache: LlmCache) -> None:
        self.puts += 1
        self.entries[cache.pattern_hash] = cache


def _make_result(title: str = "Show") -> ParseResult:
    return ParseResult(
        title=title,
        season=None,
        episode=None,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.MEDIUM,
        confidence=0.5,
        missing_fields=("season", "episode"),
        evidence={},
    )


def _recognizer(**kwargs: Any) -> LlmFallbackRecognizer:
    return LlmFallbackRecognizer(enabled=True, model="fake-model", **kwargs)


async def test_batch_success_one_transport_call_no_cache_write() -> None:
    transport = BatchScriptedTransport(
        batch_script=[_batch_payload(["a - 01", "a - 02", "a - 03"])]
    )
    cache = MemoryCacheStore()
    recognizer = _recognizer()
    raws = [RawName(name=n, folder="dir") for n in ("a - 01", "a - 02", "a - 03")]

    results = await recognizer.enhance_batch(
        raws,
        [_make_result() for _ in raws],
        None,
        transport,
        cache,
        fansub="SubA",
        operation_id="op",
    )

    assert len(transport.calls) == 1
    assert recognizer.calls_used == 1
    # 批量调用不写 llm_cache（失败项的单文件重试路径才写）。
    assert cache.puts == 0
    assert all(r is not None for r in results)
    assert results[0] is not None
    assert results[0].title == "a"
    assert results[0].episode == 1
    assert results[0].evidence.get("episode") == "llm"


async def test_batch_prompt_carries_names_and_fansub() -> None:
    transport = BatchScriptedTransport(batch_script=[_batch_payload(["a - 01", "a - 02"])])
    raws = [RawName(name=n, folder="dir") for n in ("a - 01", "a - 02")]

    await _recognizer().enhance_batch(
        raws, [_make_result() for _ in raws], None, transport, MemoryCacheStore(),
        fansub="SubA",
    )

    prompt = transport.calls[0]
    assert batch_release_names_from_prompt(prompt) == ["a - 01", "a - 02"]
    assert "Known fansub for all names: SubA" in prompt


async def test_failed_item_retries_single_file_without_guilt_by_association() -> None:
    # 批量响应第 1 项缺失（index 1 跳过）：该项单独走单文件重试，其余直接采纳。
    transport = BatchScriptedTransport(
        batch_script=[_batch_payload(["a - 01", "a - 02", "a - 03"], skip={1})],
        single_script={
            "a - 02": json.dumps(
                {"title": "a", "season": 1, "episode": 2, "segment": "episode", "fansub": None}
            )
        },
    )
    cache = MemoryCacheStore()
    recognizer = _recognizer()
    raws = [RawName(name=n, folder="dir") for n in ("a - 01", "a - 02", "a - 03")]

    results = await recognizer.enhance_batch(
        raws, [_make_result() for _ in raws], None, transport, cache,
        fansub="SubA",
    )

    # 1 次批量 + 1 次失败项单文件重试：两次都是真实调用。
    assert len(transport.calls) == 2
    assert recognizer.calls_used == 2
    # 失败项的单文件重试成功后写自己的 llm_cache；批量调用本身不写。
    assert cache.puts == 1
    assert all(r is not None for r in results)
    assert results[1] is not None
    assert results[1].episode == 2


async def test_whole_batch_invalid_falls_back_to_single_file() -> None:
    transport = BatchScriptedTransport(
        batch_script=["total garbage"],
        single_script={
            name: json.dumps(
                {"title": "a", "season": 1, "episode": i + 1, "segment": "episode"}
            )
            for i, name in enumerate(("a - 01", "a - 02"))
        },
    )
    raws = [RawName(name=n, folder="dir") for n in ("a - 01", "a - 02")]

    results = await _recognizer().enhance_batch(
        raws, [_make_result() for _ in raws], None, transport, MemoryCacheStore(),
        fansub="SubA",
    )

    assert len(transport.calls) == 3  # 1 批量 + 2 单文件
    assert all(r is not None for r in results)


async def test_duplicate_index_items_fall_back_single_file() -> None:
    # index 0 重复 → 位置 0 与 1 的输出都不可归属，两项走单文件重试。
    transport = BatchScriptedTransport(
        batch_script=[
            json.dumps(
                [
                    {"index": 0, "title": "a", "season": 1, "episode": 1, "segment": "episode"},
                    {"index": 0, "title": "a", "season": 1, "episode": 2, "segment": "episode"},
                ]
            )
        ],
        single_script={
            name: json.dumps(
                {"title": "a", "season": 1, "episode": i + 1, "segment": "episode"}
            )
            for i, name in enumerate(("a - 01", "a - 02"))
        },
    )
    raws = [RawName(name=n, folder="dir") for n in ("a - 01", "a - 02")]

    results = await _recognizer().enhance_batch(
        raws, [_make_result() for _ in raws], None, transport, MemoryCacheStore(),
        fansub="SubA",
    )

    assert len(transport.calls) == 3
    assert all(r is not None for r in results)


async def test_single_retry_failure_stays_none() -> None:
    # 失败项的单文件重试再失败（无脚本响应 → transport 抛错耗尽）→ 该项 None。
    transport = BatchScriptedTransport(
        batch_script=[_batch_payload(["a - 01", "a - 02"], skip={1})],
        single_script={},
    )
    raws = [RawName(name=n, folder="dir") for n in ("a - 01", "a - 02")]

    results = await _recognizer().enhance_batch(
        raws, [_make_result() for _ in raws], None, transport, MemoryCacheStore(),
        fansub="SubA",
    )

    assert results[0] is not None
    assert results[1] is None


async def test_disabled_or_model_missing_yields_all_none() -> None:
    transport = BatchScriptedTransport()
    raws = [RawName(name="a - 01", folder="dir"), RawName(name="a - 02", folder="dir")]
    cache: LlmCacheStore = MemoryCacheStore()

    disabled = LlmFallbackRecognizer(enabled=False, model="fake-model")
    no_model = LlmFallbackRecognizer(enabled=True, model=None)
    for recognizer in (disabled, no_model):
        results = await recognizer.enhance_batch(
            raws, [_make_result() for _ in raws], None, transport, cache, fansub="SubA"
        )
        assert results == [None, None]
    assert transport.calls == []
    assert disabled.calls_used == 0


async def test_transport_network_error_retries_then_succeeds() -> None:
    transport = BatchScriptedTransport(
        batch_script=[TimeoutError("boom"), _batch_payload(["a - 01", "a - 02"])]
    )
    raws = [RawName(name=n, folder="dir") for n in ("a - 01", "a - 02")]

    results = await _recognizer().enhance_batch(
        raws, [_make_result() for _ in raws], None, transport, MemoryCacheStore(),
        fansub="SubA",
    )

    assert len(transport.calls) == 2  # 首次超时 + 重试成功
    assert all(r is not None for r in results)


async def test_empty_batch_returns_empty_list() -> None:
    results = await _recognizer().enhance_batch(
        [], [], None, BatchScriptedTransport(), MemoryCacheStore(), fansub="SubA"
    )
    assert results == []


async def test_context_is_passed_through_to_batch_prompt() -> None:
    transport = BatchScriptedTransport(batch_script=[_batch_payload(["a - 01"])])
    raws = [RawName(name="a - 01", folder="dir")]

    await _recognizer().enhance_batch(
        raws,
        [_make_result()],
        ParseContext(release_progress=12),
        transport,
        MemoryCacheStore(),
        fansub="SubA",
    )

    assert "latest released episode: 12" in transport.calls[0]
