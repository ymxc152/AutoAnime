"""Unit tests for scripts/validate_l2_corpus.py (T6 真实快照 L2 两遍验证).

The real snapshot lives outside the repository; tests that need it skip
automatically when the file is absent.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseResult, RawName

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate_l2_corpus.py"

_SYNTHETIC_SNAPSHOT = "\n".join(
    [
        "# 合成快照",
        "[F] [hyakuhuyu&LoliHouse] BanG Dream Yumemita - 02 [WebRip 1080p HEVC-10bit AAC ASSx2].mkv",
        "",
        "[F] [hyakuhuyu&LoliHouse] BanG Dream Yumemita - 03 [WebRip 1080p HEVC-10bit AAC ASSx2].mkv",
    ]
)


def _load_script() -> Any:
    """Import scripts/validate_l2_corpus.py as a module (scripts/ is not a package)."""
    assert _SCRIPT_PATH.is_file(), f"missing script: {_SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("validate_l2_corpus", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("validate_l2_corpus", module)
    spec.loader.exec_module(module)
    return module


def _result(
    title: str,
    *,
    season: int | None = None,
    episode: int | None = None,
    fansub: str | None = None,
    level: Confidence = Confidence.MEDIUM,
) -> ParseResult:
    return ParseResult(
        title=title,
        season=season,
        episode=episode,
        segment=Segment.EPISODE,
        fansub=fansub,
        level=level,
        confidence=0.6,
        missing_fields=(),
        evidence={},
    )


def _record(mod: Any, line: int, name: str, result: ParseResult | None) -> Any:
    entry = mod.SnapshotEntry(kind="F", name=name, line_number=line)
    return mod.PassRecord(
        entry=entry,
        raw=RawName(name=name),
        result=result,
        route="l3",
        l2_applied=False,
        degraded=False,
        duration_s=0.001,
    )


# ---------------------------------------------------------------------------
# Snapshot line parsing and RawName construction
# ---------------------------------------------------------------------------


def test_parse_snapshot_lines_kinds_and_comments() -> None:
    mod = _load_script()
    entries = list(mod.parse_snapshot_lines(_SYNTHETIC_SNAPSHOT.splitlines()))
    assert [(e.kind, e.line_number) for e in entries] == [("F", 2), ("F", 4)]
    assert all("BanG Dream Yumemita" in e.name for e in entries)


def test_parse_snapshot_lines_skips_unknown_prefix_and_empty_names() -> None:
    mod = _load_script()
    entries = list(mod.parse_snapshot_lines(["[X] unknown", "[F]   ", "[D] ok"]))
    assert [(e.kind, e.name) for e in entries] == [("D", "ok")]


def test_to_raw_name_directory_is_own_folder_file_has_none() -> None:
    mod = _load_script()
    d_entry, f_entry = list(
        mod.parse_snapshot_lines(["[D] Some.Season.Pack.S01", "[F] Some.Show - 01.mp4"])
    )
    assert mod.to_raw_name(d_entry).folder == "Some.Season.Pack.S01"
    assert mod.to_raw_name(f_entry).folder is None


def test_default_snapshot_path_prefers_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _load_script()
    snapshot = tmp_path / "snapshot.txt"
    monkeypatch.setenv("AUTOANIME_L2_SNAPSHOT", str(snapshot))

    assert mod.default_snapshot_path() == snapshot


def test_default_snapshot_path_falls_back_to_repository_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script()
    monkeypatch.delenv("AUTOANIME_L2_SNAPSHOT", raising=False)

    assert mod.default_snapshot_path() == mod._ROOT.parent / "notes" / "samples" / "z_downloads_snapshot.txt"


# ---------------------------------------------------------------------------
# Confirmation clustering (season/fansub consensus rules)
# ---------------------------------------------------------------------------


def test_build_learn_plan_fills_only_unambiguous_consensus() -> None:
    mod = _load_script()
    records = [
        _record(mod, 1, "Show - 01", _result("Show Name", episode=1)),
        _record(mod, 2, "Show - 02", _result("Show Name", season=2, fansub="SubX", episode=2)),
        _record(mod, 3, "Show - 03", _result("Show Name", season=2, episode=3)),
        # 跨季混合簇：season 不补；fansub 平票：不补。
        _record(mod, 4, "Mixed - 01", _result("Mixed Show", season=1, fansub="P")),
        _record(mod, 5, "Mixed - 02", _result("Mixed Show", season=2, fansub="Q")),
        _record(mod, 6, "Mixed - 03", _result("Mixed Show")),
    ]

    plan = mod.build_learn_plan(records)

    assert plan.clusters == 2
    assert len(plan.confirmations) == 6
    assert plan.filled_season == 1
    assert plan.filled_fansub == 2

    confirmed = {entry.line_number: result for entry, result in plan.confirmations}
    assert confirmed[1].season == 2  # 簇内唯一 season 共识
    assert confirmed[1].fansub == "SubX"
    assert confirmed[2].season == 2 and confirmed[2].fansub == "SubX"  # L1 自己的值不被覆盖
    assert confirmed[4].season == 1 and confirmed[4].fansub == "P"  # 自己的值保留
    assert confirmed[6].season is None and confirmed[6].fansub is None  # 混合/平票不补

    for _, result in plan.confirmations:
        assert result.level is Confidence.HIGH
        assert result.missing_fields == ()
        assert result.evidence == {}


def test_build_learn_plan_ignores_non_medium_and_failures() -> None:
    mod = _load_script()
    records = [
        _record(mod, 1, "high", _result("High Show", season=1, level=Confidence.HIGH)),
        _record(mod, 2, "boom", None),
        _record(mod, 3, "Show - 01", _result("Show Name", episode=1)),
    ]
    records[1].error = "TypeError"

    plan = mod.build_learn_plan(records)

    assert plan.clusters == 1
    assert [entry.line_number for entry, _ in plan.confirmations] == [3]


# ---------------------------------------------------------------------------
# Two-pass roundtrip on synthetic entries (in-memory sqlite)
# ---------------------------------------------------------------------------


def test_run_two_pass_synthetic_roundtrip() -> None:
    mod = _load_script()
    entries = list(mod.parse_snapshot_lines(_SYNTHETIC_SNAPSHOT.splitlines()))

    data = asyncio.run(mod.run_two_pass(entries))

    assert data["total"] == 2
    for stage in ("pass1", "pass2"):
        stats = data[stage]
        assert stats["parsed"] + stats["returned_none"] + stats["failed"] == 2
        assert stats["failed"] == 0
    # pass1 冷启动：L2 全部未命中。
    assert data["pass1"]["l2_hit"] == 0
    assert data["pass1"]["l2_miss"] == data["pass1"]["l2_eligible"]
    # 学习后复测：同剧目的第二遍应命中记忆。
    assert data["learn"]["learn_failed"] == 0
    assert data["learn"]["rows_series"] >= 1
    assert data["pass2"]["l2_hit"] >= 1
    assert data["pass2"]["medium_to_high"] <= data["pass2"]["l2_hit"]
    assert data["pass2"]["hit_rate"] > 0
    # 输出必须是确定性 JSON。
    dumped = json.dumps(data, ensure_ascii=False, sort_keys=True)
    assert json.loads(dumped) == data


def test_run_two_pass_swallows_per_entry_exception() -> None:
    mod = _load_script()
    entries = list(mod.parse_snapshot_lines(_SYNTHETIC_SNAPSHOT.splitlines()))
    entries.append(mod.SnapshotEntry(kind="F", name=None, line_number=5))  # type: ignore[arg-type]

    data = asyncio.run(mod.run_two_pass(entries))

    assert data["total"] == 3
    assert data["pass1"]["failed"] == 1
    assert data["pass2"]["failed"] == 1
    assert data["learn"]["learn_failed"] == 0  # 异常条目不产生确认输入
    assert data["pass1"]["failed_samples"][0]["line"] == 5


def test_main_end_to_end_synthetic_snapshot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_script()
    snapshot = tmp_path / "snapshot.txt"
    snapshot.write_text(_SYNTHETIC_SNAPSHOT, encoding="utf-8")

    exit_code = mod.main(["--snapshot", str(snapshot)])
    captured = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["total"] == 2
    assert captured["snapshot"] == "snapshot.txt"
    assert captured["pass2"]["l2_hit"] >= 1


# ---------------------------------------------------------------------------
# Real external snapshot (skipped when the file is not present)
# ---------------------------------------------------------------------------


def test_real_snapshot_full_two_pass() -> None:
    mod = _load_script()
    snapshot = mod.default_snapshot_path()
    if not snapshot.is_file():
        pytest.skip(f"external snapshot not available: {snapshot}")

    entries = list(mod.parse_snapshot_lines(snapshot.read_text(encoding="utf-8-sig")))
    report = asyncio.run(mod.run_two_pass(entries))

    # Baseline established on the 2026-09-05 2606-line real snapshot.
    # Floors, not exact expectations, so small parser changes do not force a
    # test update while gross regressions are still caught.
    assert report["total"] == 2606
    assert report["pass1"]["failed"] == 0
    assert report["pass1"]["parsed"] >= 2600
    assert report["pass1"]["returned_none"] <= 3
    assert report["pass1"]["levels"]["medium"] >= 2200
    assert report["pass1"]["levels"]["high"] >= 350
    assert report["pass1"]["l2_miss"] == report["pass1"]["l2_eligible"]
    assert report["learn"]["learn_failed"] == 0
    assert report["learn"]["rows_series"] >= 200
    assert report["pass2"]["failed"] == 0
    assert report["pass2"]["hit_rate"] >= 0.9
    assert report["pass2"]["medium_to_high"] >= 1
    assert report["observations"]["high_also_lookup_estimate"]["high_results"] >= 350
    dumped = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert json.loads(dumped) == report
