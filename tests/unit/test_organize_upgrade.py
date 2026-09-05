"""organize.upgrade 单测（E4）：评分公式逐项 + 洗版触发决策表（参数化）。"""

from __future__ import annotations

import pytest

from autoanime.organize.upgrade import (
    codec_points,
    decide_upgrade,
    fansub_points,
    parse_quality_tokens,
    resolution_points,
    score_from_title,
    score_release,
    seeders_points,
    source_points,
)


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [("1080p", 4.0), ("720p", 2.0), ("576p", 1.0), ("480p", 1.0), ("2160p", 0.0), (None, 0.0)],
)
def test_resolution_points(resolution: str | None, expected: float) -> None:
    assert resolution_points(resolution) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("b-global", 3.0),
        ("baha", 3.0),
        ("atx", 3.0),
        ("web-dl", 3.0),
        ("webrip", 2.0),
        ("hdtv", 1.0),
        (None, 0.0),
    ],
)
def test_source_points(source: str | None, expected: float) -> None:
    assert source_points(source) == expected


@pytest.mark.parametrize(
    ("codec", "expected"),
    [("hevc", 2.0), ("avc", 1.0), (None, 0.0)],
)
def test_codec_points(codec: str | None, expected: float) -> None:
    assert codec_points(codec) == expected


@pytest.mark.parametrize(
    ("fansub", "pref", "expected"),
    [
        ("LoliHouse", "LoliHouse", 2.0),  # 命中（大小写无关）
        ("lolihouse", "LoliHouse", 2.0),
        ("VCB-Studio", "LoliHouse", 1.0),  # 未命中 = 默认
        (None, "LoliHouse", 1.0),
        ("LoliHouse", None, 1.0),  # 未配置偏好 = 全默认
    ],
)
def test_fansub_points(fansub: str | None, pref: str | None, expected: float) -> None:
    assert fansub_points(fansub, pref) == expected


@pytest.mark.parametrize(
    ("seeders", "expected"),
    [(None, 0.0), (0, 0.0), (9, 1.0), (99, 2.0), (10**9, 2.0)],  # 封顶 2
)
def test_seeders_points(seeders: int | None, expected: float) -> None:
    assert seeders_points(seeders) == pytest.approx(expected, abs=1e-6)


def test_parse_quality_tokens_from_real_title() -> None:
    tokens = parse_quality_tokens("[LoliHouse] 孤独摇滚 - 05 [WebRip 1080p HEVC-10bit AAC]")
    assert tokens.resolution == "1080p"
    assert tokens.source == "webrip"
    assert tokens.codec == "hevc"


def test_full_score_known_fields_maxes_13() -> None:
    score = score_release(
        resolution="1080p", source="web-dl", codec="hevc",
        fansub="LoliHouse", fansub_pref="LoliHouse", seeders=99,
    )
    assert score == pytest.approx(4 + 3 + 2 + 2 + 2)


def test_rss_unknown_seeders_still_scores_resolution_only() -> None:
    """D15：seeders 未知 → 0 分参与不剔除；候选不因字段缺失被丢弃。"""
    score = score_release(
        resolution="1080p", source=None, codec=None,
        fansub=None, fansub_pref=None, seeders=None,
    )
    assert score == pytest.approx(4 + 1)  # 分辨率 + 默认字幕组 1 分


def test_score_from_title_convenience() -> None:
    score = score_from_title(
        "[LoliHouse] Show - 05 [Baha 1080p HEVC]",
        fansub="LoliHouse", fansub_pref="LoliHouse", seeders=None,
    )
    assert score == pytest.approx(4 + 3 + 2 + 2)


# ---------------------------------------------------------------------------
# 洗版触发决策表（评分 × 阈值 × 上限，钉死）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "current", "count", "threshold", "max_upgrades", "allowed", "reason"),
    [
        # 基线：新分 ≥ 现分 + 2 才触发
        (10.0, 8.0, 0, 2.0, 2, True, "allowed"),
        (9.9, 8.0, 0, 2.0, 2, False, "threshold_not_met"),
        (10.0, 8.0, 0, 2.5, 2, False, "threshold_not_met"),  # 阈值可配
        # 上限：upgraded_count ≥ max 直接拒绝（先于阈值判定）
        (12.0, 0.0, 2, 2.0, 2, False, "upgrade_limit_reached"),
        (12.0, 0.0, 1, 2.0, 2, True, "allowed"),
        (12.0, 0.0, 5, 2.0, 2, False, "upgrade_limit_reached"),
        # 等分/降分永不触发
        (8.0, 8.0, 0, 2.0, 2, False, "threshold_not_met"),
        (5.0, 8.0, 0, 2.0, 2, False, "threshold_not_met"),
    ],
)
def test_decide_upgrade_table(
    candidate: float,
    current: float,
    count: int,
    threshold: float,
    max_upgrades: int,
    allowed: bool,
    reason: str,
) -> None:
    decision = decide_upgrade(
        candidate_score=candidate,
        current_score=current,
        upgraded_count=count,
        threshold=threshold,
        max_upgrades=max_upgrades,
    )
    assert decision.allowed is allowed
    assert decision.reason == reason
