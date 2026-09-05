"""Unit tests for scripts/validate_l3_corpus.py (T6 真实快照 L3 离线验证).

The real snapshot lives outside the repository; tests that need it skip
automatically when the file is absent. All pipeline runs are fully offline
(fake LLM transport, in-memory sqlite).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from autoanime.pipeline.l3 import parse_llm_response

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate_l3_corpus.py"

_SYNTHETIC_SNAPSHOT = "\n".join(
    [
        "# 合成快照",
        "[F] [hyakuhuyu&LoliHouse] BanG Dream Yumemita - 02 [WebRip 1080p HEVC-10bit AAC ASSx2].mkv",
        "",
        "[F] [hyakuhuyu&LoliHouse] BanG Dream Yumemita - 03 [WebRip 1080p HEVC-10bit AAC ASSx2].mkv",
        "[D] [摩绪].MAO.2026.S01.Complete.1080p.LINETV.WEB-DL.H264.AAC-UBWEB",
    ]
)


def _load_script() -> Any:
    """Import scripts/validate_l3_corpus.py as a module (scripts/ is not a package)."""
    assert _SCRIPT_PATH.is_file(), f"missing script: {_SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("validate_l3_corpus", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("validate_l3_corpus", module)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Snapshot line parsing and RawName construction
# ---------------------------------------------------------------------------


def test_parse_snapshot_lines_kinds_and_comments() -> None:
    mod = _load_script()
    entries = list(mod.parse_snapshot_lines(_SYNTHETIC_SNAPSHOT.splitlines()))
    assert [(e.kind, e.line_number) for e in entries] == [("F", 2), ("F", 4), ("D", 5)]
    assert all("BanG Dream Yumemita" in e.name or "摩绪" in e.name for e in entries)


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
    monkeypatch.setenv("AUTOANIME_L3_SNAPSHOT", str(snapshot))

    assert mod.default_snapshot_path() == snapshot


# ---------------------------------------------------------------------------
# Fake LLM response rules (deterministic extraction from the release name)
# ---------------------------------------------------------------------------


def test_build_fake_response_fields_from_release_name() -> None:
    mod = _load_script()
    cases = [
        (
            "[摩绪].MAO.2026.S01.Complete.1080p.LINETV.WEB-DL.H264.AAC-UBWEB",
            {"title": "摩绪", "season": 1, "episode": None, "segment": "season_pack", "fansub": "UBWEB"},
        ),
        (
            "[BeanSub&FZSD&LoliHouse] BLEACH Sennen Kessen-hen - 41 [WebRip 1080p HEVC-10bit AAC ASSx2]",
            {
                "title": "BLEACH Sennen Kessen-hen",
                "season": None,
                "episode": 41,
                "segment": "episode",
                "fansub": "BeanSub&FZSD&LoliHouse",
            },
        ),
        (
            "[BeanSub&LoliHouse] Tensei Shitara Slime Datta Ken 4th Season - 14(86)",
            {
                "title": "Tensei Shitara Slime Datta Ken",
                "season": 4,
                "episode": 14,
                "segment": "episode",
                "fansub": "BeanSub&LoliHouse",
            },
        ),
        (
            "[某某字幕组][某动画][第2季][01][1080p].mkv",
            {"title": "某动画", "season": 2, "episode": 1, "segment": "episode", "fansub": "某某字幕组"},
        ),
        (
            "[某某字幕组][某动画][第二季][12][1080p].mkv",
            {"title": "某动画", "season": 2, "episode": 12, "segment": "episode", "fansub": "某某字幕组"},
        ),
        (
            "[某组] 剧场版 某某 Movie 2024.mkv",
            {"title": "某组", "season": None, "episode": None, "segment": "movie", "fansub": None},
        ),
    ]
    for name, expected in cases:
        payload = json.loads(mod.build_fake_response(name))
        assert payload == expected, name


def test_build_fake_response_always_schema_valid_and_deterministic() -> None:
    mod = _load_script()
    for name in ("", "Just.Some.Weird.Name. Without Any Markers", "…garbage…"):
        response = mod.build_fake_response(name)
        draft = parse_llm_response(response)  # must not raise
        assert draft.title
        # 同一输入永远得到同一响应（可复现）。
        assert mod.build_fake_response(name) == response


def test_raw_name_from_prompt() -> None:
    mod = _load_script()
    prompt = (
        "You parse anime release names into structured metadata.\n\n"
        "Release name: Anime.Show.S01E03.mkv\nLocal parse hint: ...\n"
    )
    assert mod.raw_name_from_prompt(prompt) == "Anime.Show.S01E03.mkv"
    assert mod.raw_name_from_prompt("no release name here") == ""


async def test_fake_transport_complete_returns_valid_response() -> None:
    mod = _load_script()
    transport = mod.FakeRuleTransport()
    prompt = (
        "You parse anime release names into structured metadata.\n\n"
        "Release name: [摩绪].MAO.2026.S01.Complete.1080p.WEB-DL.H264.AAC-UBWEB\n"
    )
    response = await transport.complete(prompt, model="fake", timeout_s=1.0)

    assert len(transport.calls) == 1
    draft = parse_llm_response(response)
    assert draft.title == "摩绪"
    assert draft.season == 1


# ---------------------------------------------------------------------------
# Counting adapters
# ---------------------------------------------------------------------------


async def test_counting_cache_store_counts_hits() -> None:
    mod = _load_script()
    from autoanime.memory.store import SqliteStorage
    from autoanime.pipeline.l3 import LlmCache

    async with SqliteStorage("sqlite+aiosqlite:///:memory:") as storage:
        store = mod._CountingLlmCacheStore(storage)
        assert await store.get("missing") is None
        assert store.hits == 0
        await store.put(LlmCache(pattern_hash="k", response="{}", model="m"))
        cached = await store.get("k")
        assert cached is not None and cached.response == "{}"
        assert store.hits == 1


# ---------------------------------------------------------------------------
# Full offline pipeline roundtrip on synthetic entries
# ---------------------------------------------------------------------------


def _assert_flat_stats(mod: Any, data: dict[str, Any]) -> None:
    """Required flat keys exist and are internally consistent."""
    for key in (
        "total",
        "l3_entered",
        "l3_fake_hit",
        "arbiter_accepted",
        "arbiter_rejected",
        "arbiter_upgraded",
        "degraded",
        "cache_hit",
        "elapsed_ms",
    ):
        assert key in data, key
    assert data["l3_fake_hit"] <= data["l3_entered"] <= data["total"]
    assert data["arbiter_accepted"] + data["arbiter_rejected"] <= data["l3_fake_hit"]


def test_run_validation_synthetic_roundtrip() -> None:
    mod = _load_script()
    entries = list(mod.parse_snapshot_lines(_SYNTHETIC_SNAPSHOT.splitlines()))

    data = asyncio.run(mod.run_validation(entries))

    _assert_flat_stats(mod, data)
    assert data["total"] == 3
    # 无 L1 失败、无降级：fake transport 恒产出 schema 合法响应。
    assert data["failed"] == 0
    assert data["degraded"] == 0
    # 冷缓存 pass1 每个 L3 条目都是一次 transport 调用。
    pass1 = data["passes"]["pass1"]
    assert pass1["l3_entered"] == pass1["transport_calls"]
    assert pass1["l3_fake_hit"] == pass1["l3_entered"]
    # pass2 复用同一缓存：零 transport 调用、全部命中。
    pass2 = data["passes"]["pass2"]
    assert pass2["transport_calls"] == 0
    assert pass2["cache_hit"] == pass2["l3_entered"]
    assert pass2["l3_fake_hit"] == pass2["l3_entered"]
    # 输出必须是确定性 JSON。
    dumped = json.dumps(data, ensure_ascii=False, sort_keys=True)
    assert json.loads(dumped) == data


def test_run_validation_collects_l1_candidates() -> None:
    mod = _load_script()
    entries = list(mod.parse_snapshot_lines(_SYNTHETIC_SNAPSHOT.splitlines()))

    candidates = asyncio.run(mod.collect_llm_candidates(entries))

    assert len(candidates) <= 10
    assert all(item["l1"] in ("none", "low", "medium") for item in candidates)
    assert all({"line", "kind", "name", "l1"} <= set(item) for item in candidates)
    # none 优先于 low，low 优先于 medium 残缺（同为按行号稳定排序）。
    ranks = [{"none": 0, "low": 1, "medium": 2}[item["l1"]] for item in candidates]
    assert ranks == sorted(ranks)
    assert all("missing_fields" in item for item in candidates if item["l1"] == "medium")


def test_run_validation_swallows_per_entry_exception() -> None:
    mod = _load_script()
    entries = list(mod.parse_snapshot_lines(_SYNTHETIC_SNAPSHOT.splitlines()))
    entries.append(mod.SnapshotEntry(kind="F", name=None, line_number=5))  # type: ignore[arg-type]

    data = asyncio.run(mod.run_validation(entries))

    _assert_flat_stats(mod, data)
    assert data["total"] == 4
    assert data["failed"] == 2  # 两个 pass 各记一次
    assert data["failed_samples"][0]["line"] == 5
    # 异常条目不进入 L3 计数，其余条目照常完成。
    assert data["passes"]["pass1"]["l3_entered"] <= 3
    assert data["passes"]["pass1"]["l3_fake_hit"] == data["passes"]["pass1"]["l3_entered"]


def test_main_end_to_end_synthetic_snapshot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_script()
    snapshot = tmp_path / "snapshot.txt"
    snapshot.write_text(_SYNTHETIC_SNAPSHOT, encoding="utf-8")
    output = tmp_path / "stats.json"

    exit_code = mod.main(["--snapshot", str(snapshot), "--output", str(output)])
    captured = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["total"] == 3
    assert captured["snapshot"] == "snapshot.txt"
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["total"] == 3


# ---------------------------------------------------------------------------
# Real external snapshot (skipped when the file is not present)
# ---------------------------------------------------------------------------


def test_real_snapshot_full_l3_validation() -> None:
    mod = _load_script()
    snapshot = mod.default_snapshot_path()
    if not snapshot.is_file():
        pytest.skip(f"external snapshot not available: {snapshot}")

    entries = list(mod.parse_snapshot_lines(snapshot.read_text(encoding="utf-8-sig")))
    report = asyncio.run(mod.run_validation(entries))

    # Baseline established on the 2026-09-05 2606-line real snapshot.
    # Floors, not exact expectations, so small rule changes do not force a
    # test update while gross regressions are still caught.
    assert report["total"] == 2606
    assert report["failed"] == 0
    assert report["degraded"] == 0
    # L3 覆盖全部非 archive 条目；fake transport 恒命中。
    assert report["l3_entered"] >= 2000
    assert report["l3_fake_hit_rate"] >= 0.99
    # 采纳 + 否决 = 全部进入仲裁的 L3 条目。fake 从发布名提取的字段与
    # L1（anitopy）同源，name 证据优先级更高，采纳天然稀少（基线为 2），
    # 但升档（R4/R5）应稳定出现。
    assert report["arbiter_accepted"] + report["arbiter_rejected"] == report["l3_fake_hit"]
    assert report["arbiter_accepted"] >= 1
    assert report["arbiter_upgraded"] >= 1
    # pass2 全量缓存回放：零真实调用，命中数等于 pass2 进入 L3 的条数。
    pass2 = report["passes"]["pass2"]
    assert pass2["transport_calls"] == 0
    assert pass2["cache_hit"] == pass2["l3_entered"]
    assert report["cache_hit"] >= report["l3_entered"]
    # 真实 LLM 实测候选：共 10 条（none/low/medium 残缺），含原始文件名。
    assert len(report["real_llm_candidates"]) == 10
    dumped = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert json.loads(dumped) == report
