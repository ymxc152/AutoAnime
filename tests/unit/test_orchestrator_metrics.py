"""orchestrator parse_events 指标写侧单测（R1 验收回归，全部离线）。

修复背景：``parse_events`` 表此前没有任何生产写入路径，``autoanime
report`` 的 llm_call_rate / archived_events 分母恒为 0。修复：orchestrator
在单文件与批量两个入口的每轮 parse 结束时经 ``ParseEventSink``（
``MemoryGovernance.record_parse_event`` 结构化满足）落一行。

口径：level = HIGH 3 / MEDIUM 2 / LOW 1 / 无结果 0；llm_called =
``l3_applied``（L3 段参与并产出 draft）；outcome = 路由。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.orchestrator import (
    ROUTE_ARCHIVE,
    ROUTE_L3,
    Orchestrator,
    ParseEventSink,
)


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
class MetricsSink:
    """parse_events 写侧 fake：按 ``ParseEventSink`` 协议收集行。"""

    rows: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False

    async def record_parse_event(
        self,
        *,
        raw_name_hash: str,
        level: int,
        llm_called: bool,
        outcome: str,
        latency_ms: int | None = None,
        confidence: str | None = None,
    ) -> object:
        if self.fail:
            raise RuntimeError("sink down")
        self.rows.append(
            {
                "raw_name_hash": raw_name_hash,
                "level": level,
                "llm_called": llm_called,
                "outcome": outcome,
                "latency_ms": latency_ms,
                "confidence": confidence,
            }
        )
        return None


def _result(level: Confidence) -> ParseResult:
    return ParseResult(
        title="Show",
        season=1,
        episode=1,
        segment=Segment.EPISODE,
        fansub=None,
        level=level,
        confidence=0.9 if level is Confidence.HIGH else 0.6,
        missing_fields=(),
        evidence={},
    )


def _orchestrator(
    recognizer: FakeRecognizer, sink: MetricsSink | None
) -> Orchestrator:
    return Orchestrator(
        recognizer,  # type: ignore[arg-type]
        l2_enabled=True,
        l3_enabled=False,
        metrics_sink=sink,
    )


async def test_process_emits_parse_event_per_pass() -> None:
    """单文件路径：每轮 parse 落一行；HIGH/无结果两种形态都覆盖。"""
    sink = MetricsSink()
    recognizer = FakeRecognizer(result=_result(Confidence.HIGH))
    orch = _orchestrator(recognizer, sink)
    outcome = await orch.process(RawName(name="Show.S01E01.mkv"))
    assert outcome.route == ROUTE_ARCHIVE
    assert sink.rows == [
        {
            "raw_name_hash": sink.rows[0]["raw_name_hash"],
            "level": 3,
            "llm_called": False,
            "outcome": "archive",
            "latency_ms": sink.rows[0]["latency_ms"],
            "confidence": "0.9000",
        }
    ]
    assert sink.rows[0]["latency_ms"] >= 0
    assert len(sink.rows[0]["raw_name_hash"]) == 64  # sha256 hex

    # L1 无结果：level 0、confidence None、outcome 仍随路由。
    recognizer2 = FakeRecognizer(result=None)
    sink2 = MetricsSink()
    await _orchestrator(recognizer2, sink2).process(RawName(name="???"))
    assert sink2.rows[0]["level"] == 0
    assert sink2.rows[0]["confidence"] is None
    assert sink2.rows[0]["outcome"] == ROUTE_L3


async def test_batch_emits_parse_event_per_item() -> None:
    """批量入口：每个输入各落一行（含 L1 HIGH 直归档项）。"""
    sink = MetricsSink()
    orch = _orchestrator(FakeRecognizer(result=_result(Confidence.HIGH)), sink)
    outcomes = await orch.process_batch(
        [RawName(name="a.mkv"), RawName(name="b.mkv")], batching=True
    )
    assert len(outcomes) == 2
    assert [row["outcome"] for row in sink.rows] == ["archive", "archive"]
    assert len({row["raw_name_hash"] for row in sink.rows}) == 2


async def test_sink_failure_never_breaks_parse() -> None:
    """指标旁路失败只吞掉：主流程照常返回 RouteOutcome。"""
    sink = MetricsSink(fail=True)
    orch = _orchestrator(FakeRecognizer(result=_result(Confidence.HIGH)), sink)
    outcome = await orch.process(RawName(name="Show.S01E01.mkv"))
    assert outcome.route == ROUTE_ARCHIVE


async def _protocol_satisfied() -> None:  # pragma: no cover - 静态契约示例
    _: ParseEventSink = MetricsSink()
