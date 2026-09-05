"""Unit tests for scripts/validate_pr7_corpus.py (PR7 V1 全量快照回归验证).

The real snapshot lives outside the repository; the harness itself is fully
offline (fake LLM transport, fake reference provider, in-memory sqlite), so
the tests run it over a small synthetic corpus instead. Assertions only lock
the harness contract (importable, metric fields complete, metric invariants,
alias ring accounting); corpus-specific route numbers stay out of the tests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from autoanime.pipeline.l2 import build_title_shape

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate_pr7_corpus.py"

# 「Some Show 2019」的 L1 draft shape（含年份）与 fake canonical shape（年份被
# 规则截掉）确定性地不同：种子 alias 表后，测量 pass 走 alias 环命中。
_SYNTHETIC_SNAPSHOT = "\n".join(
    [
        "# 合成快照",
        "[F] Some Show 2019 - 01.mp4",
        "[F] Some Show 2019 - 02.mp4",
        "[F] [Sub] Frieren: Beyond Journey's End - 01 [1080p].mkv",
        "[D] [摩绪].MAO.2026.S01.Complete.1080p.LINETV.WEB-DL.H264.AAC-UBWEB",
    ]
)

_REQUIRED_KEYS = (
    "total",
    "note",
    "routes",
    "l3_entered",
    "canonical_requery_hit",
    "canonical_requery_attempted",
    "alias_hit",
    "canonical_chain_hit",
    "direct_l2_hit",
    "alias_db_lookups",
    "alias_db_hits",
    "reference_provider_calls",
    "disambig_provider_calls",
    "arbiter_provider_calls",
    "alias_ring_zero_provider_calls",
    "degraded",
    "failed",
    "transport_calls",
    "duration_ms",
    "timing",
    "seed",
    "baseline_pr5_t6",
    "comparison",
    "acceptance",
)


def _load_script() -> Any:
    """Import scripts/validate_pr7_corpus.py as a module (scripts/ is not a package)."""
    assert _SCRIPT_PATH.is_file(), f"missing script: {_SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("validate_pr7_corpus", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("validate_pr7_corpus", module)
    spec.loader.exec_module(module)
    return module


def test_baseline_constants_match_pr5_t6() -> None:
    mod = _load_script()
    baseline = mod.PR5_T6_BASELINE
    assert baseline["routes"] == {"archive": 373, "memory": 0, "l3": 2233}
    assert baseline["l3_entered"] == 2233


async def test_run_validation_metric_fields_and_invariants() -> None:
    mod = _load_script()
    entries = list(mod.load_l3_corpus_module().parse_snapshot_lines(_SYNTHETIC_SNAPSHOT.splitlines()))
    report: dict[str, Any] = await mod.run_validation(entries)

    for key in _REQUIRED_KEYS:
        assert key in report, f"missing metric field: {key}"

    # 路由闭合：archive/memory/l3 之和等于全部条目，无失败、无降级。
    routes = report["routes"]
    assert routes["archive"] + routes["memory"] + routes["l3"] == report["total"]
    assert report["failed"] == 0
    assert report["degraded"] == 0

    # memory 命中口径闭合：消歧链路命中 + 经典 L2 直达命中 = routes.memory。
    assert report["canonical_requery_hit"] + report["direct_l2_hit"] == routes["memory"]
    assert report["canonical_chain_hit"] + report["alias_hit"] == report["canonical_requery_hit"]

    # alias 环零外呼：每个 alias 环命中的 pass 消歧窗口内外呼为 0。
    assert report["alias_hit"] > 0
    assert report["alias_ring_zero_provider_calls"] == report["alias_hit"]

    # 本合成语料的确定性结果：两条「Some Show 2019」经 alias 环命中 memory。
    assert report["alias_hit"] == 2
    assert routes["memory"] >= 2

    # 验收块与基线对比块结构齐全。
    assert set(report["acceptance"]) == {
        "routes_memory_ge_500",
        "canonical_requery_equals_memory",
        "alias_ring_zero_provider_calls",
        "archive_untouched",
        "l3_fallback_reduced",
    }
    assert report["baseline_pr5_t6"]["routes"]["archive"] == 373
    assert "routes.l3" in report["note"]


async def test_run_validation_offline_zero_network() -> None:
    # fake transport/reference 全程零网络：跑通即证明（无真实 transport 注入点）。
    mod = _load_script()
    entries = list(mod.load_l3_corpus_module().parse_snapshot_lines(_SYNTHETIC_SNAPSHOT.splitlines()))
    report: dict[str, Any] = await mod.run_validation(entries)
    # L3 走 fake transport 的调用数 = 进入 L3 段的条数（arbiter 输入口径）。
    assert report["l3_entered"] == report["transport_calls"]
    assert report["timing"]["total_ms"] > 0


def test_main_writes_report_json(tmp_path: Path) -> None:
    mod = _load_script()
    snapshot = tmp_path / "snapshot.txt"
    snapshot.write_text(_SYNTHETIC_SNAPSHOT, encoding="utf-8")
    output = tmp_path / "report.json"

    exit_code = mod.main(["--snapshot", str(snapshot), "--output", str(output)])

    assert exit_code == 0
    rendered = json.loads(output.read_text(encoding="utf-8"))
    assert rendered["snapshot"] == "snapshot.txt"
    assert rendered["total"] == 4


def test_main_missing_snapshot_exits_two(tmp_path: Path) -> None:
    mod = _load_script()
    assert mod.main(["--snapshot", str(tmp_path / "absent.txt")]) == 2


@pytest.mark.parametrize(
    ("shape", "expected_hit"),
    [
        ("Some Show 2019", True),  # alias 环：draft shape → canonical shape
        ("Some Show", False),  # canonical 自身不入 alias 表（self 映射跳过）
    ],
)
def test_alias_table_seed_shape_semantics(shape: str, expected_hit: bool) -> None:
    # 种子 alias 表的键值语义与 M3 confirm 回填一致：alias shape → canonical
    # shape，canonical 自身不写（put_alias_map 跳过 self 映射）。
    mod = _load_script()
    l3 = mod.load_l3_corpus_module()

    async def scenario() -> bool:
        from autoanime.memory.store import SqliteStorage

        async with SqliteStorage("sqlite+aiosqlite:///:memory:") as storage:
            provider = mod.SeededReferenceProvider()
            entries = list(
                l3.parse_snapshot_lines(["[F] Some Show 2019 - 01.mp4"])
            )
            await mod.seed_memory(
                entries,
                storage,
                provider,
                build_fake_response=l3.build_fake_response,
                to_raw_name=l3.to_raw_name,
            )
            return (
                await storage.find_alias_key(build_title_shape(shape)) is not None
            )

    import asyncio

    assert asyncio.run(scenario()) is expected_hit
