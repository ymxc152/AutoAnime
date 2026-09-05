"""E1：CLI ``report`` 子命令（库内指标汇总 + 人工介入率口径）单元测试。

覆盖：

- ``_aggregate_report`` 纯聚合：按日聚合/LLM 调用率/outcome 分布/audit
  by_action/by_actor；
- 人工介入率口径 = audit 中 ``actor == manual`` 行数 / parse_events 中
  ``outcome == archive`` 行数；分母为 0 → rate 为 null（M4 整理器落地前
  的真实状态，报告须如实呈现而非报错）；
- ``_report`` 端到端（tmp SQLite + 真实模型行）：``--json`` 输出与退出码，
  库未初始化时的可操作报错。

全部离线：不触网络、不依赖外部快照。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pytest

from autoanime import cli
from autoanime.config import Settings
from autoanime.core.enums import Actor
from autoanime.core.models import AuditLog, ParseEvents
from autoanime.memory.store import SqliteStorage


@dataclass
class FakeEventRow:
    """``ParseEvents`` 形状的聚合输入（纯聚合测试用，隔离模型细节）。"""

    event_date: date
    level: int
    llm_called: bool
    outcome: str
    latency_ms: int | None = None
    raw_name_hash: str = "h"
    confidence: str | None = None


@dataclass
class FakeAuditRow:
    """``AuditLog`` 形状的聚合输入。"""

    action: str
    actor: Actor = Actor.AUTO
    entity: str = "parse_memory"
    operation_id: str = "op"
    instruction: dict[str, object] = field(default_factory=dict)
    reverse: dict[str, object] = field(default_factory=dict)


def _events() -> list[FakeEventRow]:
    day1 = date(2026, 9, 1)
    return [
        FakeEventRow(event_date=day1, level=2, llm_called=False, outcome="archive", latency_ms=10),
        FakeEventRow(event_date=day1, level=1, llm_called=True, outcome="memory", latency_ms=30),
        FakeEventRow(event_date=day1, level=0, llm_called=True, outcome="l3", latency_ms=None),
        FakeEventRow(
            event_date=date(2026, 9, 2), level=1, llm_called=False, outcome="archive", latency_ms=20
        ),
    ]


def _audits() -> list[FakeAuditRow]:
    return [
        FakeAuditRow(action="memory_hit", actor=Actor.AUTO),
        FakeAuditRow(action="correct", actor=Actor.MANUAL),
        FakeAuditRow(action="bypass_add", actor=Actor.MANUAL),
    ]


def test_aggregate_report_daily_block_and_llm_rate() -> None:
    report = cli._aggregate_report(_events(), _audits())

    parse = report["parse_events"]
    assert parse["total"] == 4
    assert parse["llm_called_total"] == 2
    assert parse["llm_call_rate"] == 0.5
    assert parse["by_outcome"] == {"archive": 2, "l3": 1, "memory": 1}
    days = parse["days"]
    assert [day["date"] for day in days] == ["2026-09-01", "2026-09-02"]
    assert days[0]["events"] == 3
    assert days[0]["llm_call_rate"] == round(2 / 3, 4)
    assert days[0]["by_level"] == {"0": 1, "1": 1, "2": 1}
    # latency 均值只统计非空行（30 + 10）/ 2。
    assert days[0]["avg_latency_ms"] == 20.0
    assert days[1]["avg_latency_ms"] == 20.0


def test_aggregate_report_manual_intervention_rate() -> None:
    report = cli._aggregate_report(_events(), _audits())

    rate_block = report["manual_intervention_rate"]
    # 2 个 manual audit 行 / 2 个归档事件。
    assert rate_block["manual_correction_events"] == 2
    assert rate_block["archived_events"] == 2
    assert rate_block["rate"] == 1.0
    assert report["audit"]["by_actor"] == {"auto": 1, "manual": 2}
    assert report["audit"]["by_action"] == {"bypass_add": 1, "correct": 1, "memory_hit": 1}


def test_aggregate_report_zero_archived_yields_null_rate() -> None:
    events = [
        FakeEventRow(event_date=date(2026, 9, 1), level=1, llm_called=True, outcome="memory")
    ]
    report = cli._aggregate_report(events, _audits())

    rate_block = report["manual_intervention_rate"]
    assert rate_block["archived_events"] == 0
    assert rate_block["rate"] is None
    # 分母为 0 不吞掉分子口径：manual 事件数照常输出。
    assert rate_block["manual_correction_events"] == 2


def test_aggregate_report_empty_inputs() -> None:
    report = cli._aggregate_report([], [])
    assert report["generated_from"] == {"parse_events": 0, "audit_log": 0}
    assert report["parse_events"]["days"] == []
    assert report["manual_intervention_rate"]["rate"] is None


def _report_args(*, as_json: bool) -> argparse.Namespace:
    return argparse.Namespace(command="report", json=as_json)


async def _seed_db(url: str) -> None:
    async with SqliteStorage(url) as storage:
        await storage.create_all()
        await storage.add(
            ParseEvents(
                event_date=date(2026, 9, 1),
                raw_name_hash="hash-a",
                level=2,
                llm_called=False,
                latency_ms=12,
                outcome="archive",
            )
        )
        await storage.add(
            ParseEvents(
                event_date=date(2026, 9, 1),
                raw_name_hash="hash-b",
                level=1,
                llm_called=True,
                outcome="l3",
                confidence="medium",
            )
        )
        await storage.add(
            AuditLog(
                operation_id="op-1",
                entity="parse_memory",
                action="correct",
                instruction={},
                reverse={},
                actor=Actor.MANUAL,
            )
        )
        await storage.add(
            AuditLog(
                operation_id="op-2",
                entity="parse_memory",
                action="memory_hit",
                instruction={},
                reverse={},
                actor=Actor.AUTO,
            )
        )


async def test_report_json_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'report.db').as_posix()}"
    await _seed_db(url)
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(database_url=url))

    rc = await cli._report(_report_args(as_json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["generated_from"] == {"parse_events": 2, "audit_log": 2}
    assert payload["parse_events"]["by_outcome"] == {"archive": 1, "l3": 1}
    assert payload["manual_intervention_rate"]["manual_correction_events"] == 1
    assert payload["manual_intervention_rate"]["archived_events"] == 1
    assert payload["manual_intervention_rate"]["rate"] == 1.0


async def test_report_text_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'report.db').as_posix()}"
    await _seed_db(url)
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(database_url=url))

    rc = await cli._report(_report_args(as_json=False))

    assert rc == 0
    out = capsys.readouterr().out
    assert "manual intervention rate" in out
    assert "parse_events: 2 events" in out


async def test_report_on_uninitialized_db_fails_with_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # schema 不存在（report 只读，不替用户建库）：给出 init-db 提示而非 traceback。
    url = f"sqlite+aiosqlite:///{(tmp_path / 'empty.db').as_posix()}"
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(database_url=url))

    rc = await cli._report(_report_args(as_json=True))

    assert rc == 1
    assert "init-db" in capsys.readouterr().out


async def test_report_on_empty_initialized_db_reports_null_rate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # init-db 之后的空库：零指标如实呈现，rate=null（M4 落地前的真实状态）。
    url = f"sqlite+aiosqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    async with SqliteStorage(url) as storage:
        await storage.create_all()
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(database_url=url))

    rc = await cli._report(_report_args(as_json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["generated_from"] == {"parse_events": 0, "audit_log": 0}
    assert payload["manual_intervention_rate"]["rate"] is None


def test_aggregate_report_serializable() -> None:
    payload = cli._aggregate_report(
        [
            FakeEventRow(
                event_date=datetime(2026, 9, 1).date(),
                level=2,
                llm_called=False,
                outcome="archive",
            )
        ],
        [FakeAuditRow(action="memory_hit")],
    )
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "manual_intervention_rate" in rendered
