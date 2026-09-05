"""E1 ``validate_metrics`` 单元测试：合成条目端到端 + 纯函数口径。

- 合成快照条目（本地 dataclass，鸭子类型满足 ``SnapshotEntry`` 形状）
  走完整 ``run_validation``：种子记忆库 + process_batch 合批入口 + 单文件
  参考口径，全程离线零网络，不依赖外部快照文件；
- 真实快照的冒烟（skipif 无外部快照）：只跑前 60 行保持套件速度；
- 两种 ``folder_strategy`` 的分批差异被钉死：``title``（同发布目录代理）
  只有同番同组凑批；``root``（同下载根）跨番同组凑批。

决策契约（事前定死，9.3b）：

- 合成 15 条：8× Show A（同番同组 MEDIUM）→ 1 批 8 项；4× Show B（同组
  另一番，4 < min 5）→ 单文件快路径；1× Show A HIGH（L1 直达归档，永不
  进批）；2× [D] 目录条目（folder=自身唯一，永不凑批）；
- 合成名的 L1 title 与 fake LLM canonical title 一致 → 记忆命中全部为
  direct L2 命中（canonical/alias 环口径由真实快照冒烟覆盖）。
- ``llm_calls.saved_by_batching`` = single 基线 − batch 实测（合批收益的
  唯一口径；memory 命中项 L3 照常运行——PR5 契约——故单文件基线不为 0）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _ROOT / "scripts" / "validate_metrics.py"


def _load_metrics_module() -> Any:
    """Import scripts/validate_metrics.py（scripts 不是包，按路径加载）。"""
    assert _SCRIPT_PATH.is_file(), f"missing script: {_SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("validate_metrics_under_test", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("validate_metrics_under_test", module)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class SyntheticEntry:
    """``SnapshotEntry`` 形状的合成条目（kind/name/line_number 鸭子类型）。"""

    kind: str
    name: str
    line_number: int


_SHOW_A = "[ANi] Test Show A - {ep} [1080P][Baha][WEB-DL][AAC AVC][CHT].mp4"
_SHOW_B = "[ANi] Test Show B - {ep} [1080P][Baha][WEB-DL][AAC AVC][CHT].mp4"
_DIR_A = "[BeanSub&LoliHouse] Test Dir A - 01 [WebRip 1080p HEVC-10bit AAC ASSx2]"
_DIR_B = "[BeanSub&LoliHouse] Test Dir B - 01 [WebRip 1080p HEVC-10bit AAC ASSx2]"


def _synthetic_entries() -> list[SyntheticEntry]:
    """8 同番同组 + 4 同组另一番 + 1 同番 HIGH + 2 目录条目（共 11 条）。"""
    entries: list[SyntheticEntry] = []
    line = 1
    # HIGH 直达归档：永不进批（也不进 L3）。
    entries.append(SyntheticEntry("F", _SHOW_A.format(ep="S01E01"), line))
    line += 1
    # Show A：8 个 MEDIUM（同 title 同 fansub）→ title 策略下凑成 1 批。
    for ep in range(2, 10):
        entries.append(SyntheticEntry("F", _SHOW_A.format(ep=f"{ep:02d}"), line))
        line += 1
    # Show B：4 个 MEDIUM，同组不同番——title 策略下 4 < min 5 不凑批。
    for ep in range(2, 6):
        entries.append(SyntheticEntry("F", _SHOW_B.format(ep=f"{ep:02d}"), line))
        line += 1
    # 目录条目：folder=自身（唯一），永不凑批。
    entries.append(SyntheticEntry("D", _DIR_A, line))
    line += 1
    entries.append(SyntheticEntry("D", _DIR_B, line))
    return entries


# --- 纯函数口径 ---------------------------------------------------------------


def test_split_transport_calls_distinguishes_batch_and_single() -> None:
    metrics = _load_metrics_module()
    from autoanime.pipeline.l3.prompt import build_batch_prompt, build_prompt

    single = build_prompt("[Sub] Show - 01.mkv", None, None)
    batch = build_batch_prompt(["Show - 01.mkv", "Show - 02.mkv"], fansub="Sub")
    counts = metrics._split_transport_calls([single, batch, single])
    assert counts == {
        "total": 3,
        "batch_calls": 1,
        "single_calls": 2,
        "batched_items": 2,
        "batch_size_max": 2,
    }


def test_duration_percentiles_empty_and_basic() -> None:
    metrics = _load_metrics_module()
    assert metrics._duration_percentiles([]) == {
        "avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "total_s": 0.0
    }
    stats = metrics._duration_percentiles([0.001, 0.002, 0.003, 0.004, 0.100])
    assert stats["max_ms"] == 100.0
    assert stats["p50_ms"] == 3.0
    assert stats["avg_ms"] == 22.0


def test_fake_batch_transport_arrays_align_with_index() -> None:
    metrics = _load_metrics_module()
    pr7, l3 = metrics.load_modules()
    import asyncio

    transport = metrics.FakeBatchRuleTransport(
        l3.build_fake_response, l3.raw_name_from_prompt
    )
    names = ["[Sub] Show A - 01.mkv", "[Sub] Show A - 02.mkv"]
    from autoanime.pipeline.l3.prompt import build_batch_prompt

    response = asyncio.run(
        transport.complete(build_batch_prompt(names), model="m", timeout_s=1.0)
    )
    payload = json.loads(response)
    assert [item["index"] for item in payload] == [0, 1]
    assert payload[0]["title"] == payload[1]["title"]
    # 单文件 prompt 走原规则（单对象响应）。
    from autoanime.pipeline.l3.prompt import build_prompt

    single = asyncio.run(
        transport.complete(build_prompt(names[0], None, None), model="m", timeout_s=1.0)
    )
    assert isinstance(json.loads(single), dict)


# --- 合成条目端到端 -----------------------------------------------------------


def test_run_validation_title_strategy_batches_same_show() -> None:
    metrics = _load_metrics_module()
    entries = _synthetic_entries()

    report = _run(metrics, entries, folder_strategy="title")

    assert report["total"] == 15
    # L1 HIGH 直达归档；其余 14 条全部进入 L3 段（memory 命中项照常——PR5）。
    assert report["routes"]["archive"] == 1
    assert report["l1_high"] == 1
    assert report["l1_levels"]["high"] == 1
    assert report["l3_entered"] == 14
    assert report["routes"]["memory"] + report["routes"]["l3"] == 14
    # 合批核心：同番同组 8 项凑成 1 批；Show B（4 < min）与目录条目走单文件。
    batching = report["batching"]
    assert batching["batch_calls"] == 1
    assert batching["batched_items"] == 8
    assert batching["batch_applied_outcomes"] == 8
    assert batching["single_l3_calls_incl_retries"] == 6
    assert batching["max_batch_size_observed"] == 8
    # LLM 调用：批量 1 次 + 单文件 6 次；单文件基线 14 次（14 个 L3 候选）。
    assert report["llm_calls"]["batch_pass"]["batch_calls"] == 1
    assert report["llm_calls"]["batch_pass"]["transport_calls"] == 7
    assert report["llm_calls"]["single_pass"]["transport_calls"] == 14
    assert report["llm_calls"]["saved_by_batching"] == 7
    # PR7 消歧口径守恒：l2_hit = direct + canonical（canonical 含 alias 环）。
    assert report["l2_hit"] == report["direct_l2_hit"] + report["canonical_hit"]
    assert report["canonical_hit"] == report["canonical_chain_hit"] + report["alias_hit"]
    # 失败为零、时延口径可测。
    assert report["failed"] == {"batch_pass": 0, "single_pass": 0}
    assert report["p50_p95_ms"]["p50_ms"] >= 0
    assert "p95_ms" in report["latency_ms"]["batch_pass"]["l1_parse"]


def test_run_validation_root_strategy_batches_across_titles() -> None:
    metrics = _load_metrics_module()
    entries = _synthetic_entries()

    report = _run(metrics, entries, folder_strategy="root")

    # root 策略：12 个同组 MEDIUM 散文件共享合成根目录 → 1 批 12 项（≤max 20）。
    batching = report["batching"]
    assert batching["batch_calls"] == 1
    assert batching["batched_items"] == 12
    assert batching["max_batch_size_observed"] == 12
    assert batching["single_l3_calls_incl_retries"] == 2  # 两个目录条目
    # 单文件基线 14 次（14 个 L3 候选）− 批量实测 3 次（1 批 + 2 单）。
    assert report["llm_calls"]["saved_by_batching"] == 14 - 3
    assert report["failed"] == {"batch_pass": 0, "single_pass": 0}


def _run(metrics: Any, entries: list[SyntheticEntry], *, folder_strategy: str) -> dict[str, Any]:
    import asyncio

    return asyncio.run(
        metrics.run_validation(entries, folder_strategy=folder_strategy)
    )


# --- 真实快照冒烟（无外部快照环境 skip） ---------------------------------------


def _real_snapshot_path() -> Path | None:
    metrics = _load_metrics_module()
    _, l3 = metrics.load_modules()
    snapshot = l3.default_snapshot_path()
    return snapshot if snapshot.is_file() else None


@pytest.mark.skipif(_real_snapshot_path() is None, reason="外部快照不可用（无 notes 样本环境）")
def test_run_validation_real_snapshot_slice_smoke() -> None:
    metrics = _load_metrics_module()
    _, l3 = metrics.load_modules()
    snapshot = l3.default_snapshot_path()
    lines = snapshot.read_text(encoding="utf-8-sig").splitlines()[:80]
    entries = list(l3.parse_snapshot_lines("\n".join(lines)))

    report = _run(metrics, entries, folder_strategy="title")

    assert report["total"] == len(entries)
    assert sum(report["routes"].values()) + report["failed"]["batch_pass"] == len(entries)
    # 合批与调用口径互相咬合：节省 = 基线 − 实测，且实测 ≤ 基线。
    assert report["llm_calls"]["batch_pass"]["transport_calls"] <= (
        report["llm_calls"]["single_pass"]["transport_calls"]
    )
    assert report["llm_calls"]["saved_by_batching"] == (
        report["llm_calls"]["single_pass"]["transport_calls"]
        - report["llm_calls"]["batch_pass"]["transport_calls"]
    )
    assert report["failed"]["single_pass"] == 0
    json.dumps(report, ensure_ascii=False)  # 全量 JSON 可序列化
