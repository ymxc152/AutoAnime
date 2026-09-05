"""Unit tests for scripts/validate_l1_corpus.py (T6 真实快照 L1 验证).

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

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate_l1_corpus.py"


def _load_script() -> Any:
    """Import scripts/validate_l1_corpus.py as a module (scripts/ is not a package)."""
    assert _SCRIPT_PATH.is_file(), f"missing script: {_SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("validate_l1_corpus", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("validate_l1_corpus", module)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Snapshot line parsing and RawName construction
# ---------------------------------------------------------------------------


def test_parse_snapshot_lines_kinds_and_comments() -> None:
    mod = _load_script()
    text = "\n".join(
        [
            "# 注释行",
            "[D] Bleach.S01.1080p.Baha.WEB-DL.AAC2.0.H.264-MWeb",
            "",
            "[F] [ANi] 碧藍航線 微速前進！2！！ - 02 [1080P][Baha][WEB-DL][AAC AVC][CHT].mp4",
        ]
    )
    entries = list(mod.parse_snapshot_lines(text.splitlines()))
    assert [(e.kind, e.name) for e in entries] == [
        ("D", "Bleach.S01.1080p.Baha.WEB-DL.AAC2.0.H.264-MWeb"),
        ("F", "[ANi] 碧藍航線 微速前進！2！！ - 02 [1080P][Baha][WEB-DL][AAC AVC][CHT].mp4"),
    ]
    assert entries[0].line_number == 2
    assert entries[1].line_number == 4


def test_parse_snapshot_lines_skips_unknown_prefix_and_empty_names() -> None:
    mod = _load_script()
    entries = list(mod.parse_snapshot_lines(["[X] unknown", "[F]   ", "[D] ok"]))
    assert [(e.kind, e.name) for e in entries] == [("D", "ok")]


def test_to_raw_name_directory_is_own_folder_file_has_none() -> None:
    mod = _load_script()
    d_entry, f_entry = list(
        mod.parse_snapshot_lines(["[D] Some.Season.Pack.S01", "[F] Some.Show - 01.mp4"])
    )
    d_raw = mod.to_raw_name(d_entry)
    assert d_raw.name == "Some.Season.Pack.S01"
    assert d_raw.folder == "Some.Season.Pack.S01"
    f_raw = mod.to_raw_name(f_entry)
    assert f_raw.name == "Some.Show - 01.mp4"
    assert f_raw.folder is None


# ---------------------------------------------------------------------------
# Statistics report on synthetic entries
# ---------------------------------------------------------------------------


def test_validate_entries_counts_levels_segments_and_none() -> None:
    mod = _load_script()
    entries = list(
        mod.parse_snapshot_lines(
            [
                "[D] Bleach.S01.1080p.Baha.WEB-DL.AAC2.0.H.264-MWeb",
                "[F] [ANi] 碧藍航線 微速前進！2！！ - 02 [1080P][Baha][WEB-DL][AAC AVC][CHT].mp4",
                "[D] 完全无法解析的乱码名%%%",
            ]
        )
    )
    report = asyncio.run(mod.validate_entries(entries))
    assert report["total"] == 3
    assert report["parsed"] + report["returned_none"] + report["failed"] == 3
    assert set(report["levels"]) == {"high", "medium", "low"}
    assert set(report["segments"]) == {"season_pack", "episode", "movie"}
    assert isinstance(report["missing_fields"], dict)
    assert isinstance(report["max_duration_ms"], float)
    # 输出必须是确定性 JSON
    dumped = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert json.loads(dumped) == report


def test_validate_entries_swallows_per_entry_exception() -> None:
    mod = _load_script()

    class _Boom:
        """Entry whose name breaks parsing inside the recognizer path."""

        kind = "F"
        name = None  # type: ignore[assignment]  # 故意非法输入
        line_number = 1

    report = asyncio.run(mod.validate_entries([_Boom()]))
    assert report["total"] == 1
    assert report["failed"] == 1
    assert report["parsed"] == 0
    assert len(report["failed_samples"]) == 1
    assert report["failed_samples"][0]["line"] == 1


def test_validate_entries_failed_samples_are_capped() -> None:
    mod = _load_script()

    class _Boom:
        kind = "F"
        name = None  # type: ignore[assignment]  # 故意非法输入
        line_number = 1

    entries = [_Boom() for _ in range(15)]
    report = asyncio.run(mod.validate_entries(entries))
    assert report["failed"] == 15
    assert len(report["failed_samples"]) == 10


# ---------------------------------------------------------------------------
# Real external snapshot (skipped when the file is not present)
# ---------------------------------------------------------------------------


def test_real_snapshot_full_run() -> None:
    mod = _load_script()
    snapshot = mod.DEFAULT_SNAPSHOT
    if not snapshot.is_file():
        pytest.skip(f"external snapshot not available: {snapshot}")
    entries = list(mod.parse_snapshot_lines(snapshot.read_text(encoding="utf-8-sig")))
    report = asyncio.run(mod.validate_entries(entries))
    assert report["total"] > 2000
    assert report["parsed"] + report["returned_none"] + report["failed"] == report["total"]
    assert report["failed"] == 0, f"unexpected failures: {report['failed_samples']}"
    dumped = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert json.loads(dumped) == report
