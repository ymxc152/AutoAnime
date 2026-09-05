"""orchestrator expected 接线单测（E4b）：快路径短路 + 对齐结论透传（D13）。"""

from __future__ import annotations

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.organize.expected import ExpectedContext
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.orchestrator import ROUTE_ARCHIVE, Orchestrator


class ScriptedRecognizer:
    """固定返回预置结果（隔离 L1 方言细节）。"""

    def __init__(self, result: ParseResult | None) -> None:
        self._result = result

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        return self._result


def _expected(episode_number: int = 5) -> ExpectedContext:
    return ExpectedContext(
        series_id=1,
        season_number=1,
        episode_number=episode_number,
        title_cn="孤独摇滚",
        title_jp="ぼっち・ざ・ろっく!",
        torrent_hash="a" * 40,
    )


def _medium_result() -> ParseResult:
    return ParseResult(
        title="ぼっち・ざ・ろっく!",  # 中文环境 L1 也可能给出 MEDIUM 的异语言标题
        season=None,
        episode=5,
        segment=Segment.EPISODE,
        fansub="LoliHouse",
        level=Confidence.MEDIUM,
        confidence=0.4,
        missing_fields=("season",),
    )


@pytest.mark.asyncio
async def test_fast_path_lifts_medium_to_high_and_skips_l2() -> None:
    """对齐一致：MEDIUM 也直接 HIGH 快路径（跳过 L2 查找与 API 匹配）。"""
    orchestrator = Orchestrator(
        recognizer=ScriptedRecognizer(_medium_result()),
        l2_enabled=True,  # L2 开着也不该被查（快路径在前）
        l3_enabled=False,
    )
    outcome = await orchestrator.process(RawName(name="x"), expected=_expected(5))
    assert outcome.fast_path is True
    assert outcome.alignment == "fast_path"
    assert outcome.route == ROUTE_ARCHIVE
    assert outcome.result is not None
    assert outcome.result.level is Confidence.HIGH
    assert outcome.l2_applied is False


@pytest.mark.asyncio
async def test_conflict_falls_through_pipeline_with_alignment_marked() -> None:
    """冲突（异番）：不短路，走既有管线；对齐结论随 outcome 交错配恢复。"""
    wrong = ParseResult(
        title="葬送的芙莉莲",
        season=1,
        episode=5,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.HIGH,  # L1 本身很高但指向另一部番
        confidence=0.99,
    )
    orchestrator = Orchestrator(recognizer=ScriptedRecognizer(wrong), l2_enabled=False, l3_enabled=False)
    outcome = await orchestrator.process(RawName(name="x"), expected=_expected(5))
    assert outcome.fast_path is False
    assert outcome.alignment == "conflict"
    assert outcome.result == wrong  # 文件名优先：结果不被 expected 改写


@pytest.mark.asyncio
async def test_episode_variant_falls_through() -> None:
    other_episode = ParseResult(
        title="孤独摇滚",
        season=1,
        episode=6,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.HIGH,
        confidence=0.99,
    )
    orchestrator = Orchestrator(recognizer=ScriptedRecognizer(other_episode), l2_enabled=False)
    outcome = await orchestrator.process(RawName(name="x"), expected=_expected(5))
    assert outcome.alignment == "episode_variant"
    assert outcome.fast_path is False


@pytest.mark.asyncio
async def test_unparsed_falls_through() -> None:
    orchestrator = Orchestrator(recognizer=ScriptedRecognizer(None), l2_enabled=False)
    outcome = await orchestrator.process(RawName(name="x"), expected=_expected(5))
    assert outcome.alignment == "unparsed"
    assert outcome.result is None


@pytest.mark.asyncio
async def test_expected_none_keeps_legacy_behavior() -> None:
    """手动导入路径 expected=None：L1 HIGH 照旧直接归档，无对齐字段。"""
    orchestrator = Orchestrator(recognizer=LocalRecognizer(), l2_enabled=False)
    outcome = await orchestrator.process(RawName(name="[Sub] 孤独摇滚 - 05 [1080p]"))
    assert outcome.fast_path is False
    assert outcome.alignment is None


@pytest.mark.asyncio
async def test_fast_path_writes_audit_row() -> None:
    rows: list[dict[str, object]] = []

    class Sink:
        async def record_audit(self, **kwargs: object) -> None:
            rows.append(dict(kwargs))

    orchestrator = Orchestrator(
        recognizer=ScriptedRecognizer(_medium_result()),
        l2_enabled=False,
        audit_sink=Sink(),  # type: ignore[arg-type]
    )
    await orchestrator.process(RawName(name="x"), expected=_expected(5))
    assert rows and rows[0]["action"] == "subscribed_fast_path"
    instruction = rows[0]["instruction"]
    assert isinstance(instruction, dict)
    assert instruction["episode_number"] == 5
    assert instruction["torrent_hash"] == "a" * 40
