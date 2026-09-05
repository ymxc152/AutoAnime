"""organize.expected 单测（E4）：对齐校验出口 + 错配 A/B/C 决策表（参数化）。"""

from __future__ import annotations

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseResult
from autoanime.organize.expected import (
    ExpectedContext,
    MismatchEvidence,
    align_rss_entry,
    align_with_expected,
    decide_mismatch,
    title_matches,
)


def _parse(
    title: str = "孤独摇滚",
    season: int | None = 1,
    episode: int | None = 5,
    segment: Segment = Segment.EPISODE,
    level: Confidence = Confidence.HIGH,
) -> ParseResult:
    return ParseResult(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub="LoliHouse",
        level=level,
        confidence=0.99,
    )


def _expected(episode_number: int = 5) -> ExpectedContext:
    return ExpectedContext(
        series_id=1,
        season_number=1,
        episode_number=episode_number,
        title_cn="孤独摇滚",
        title_jp="ぼっち・ざ・ろっく!",
        title_romaji="Bocchi the Rock",
    )


# ---------------------------------------------------------------------------
# 对齐校验
# ---------------------------------------------------------------------------


def test_align_fast_path_exact_match() -> None:
    alignment = align_with_expected(_parse(), _expected(5))
    assert alignment.verdict == "fast_path"
    assert alignment.parsed_episode == 5


def test_align_fast_path_title_variant_and_missing_season() -> None:
    parse = _parse(title="ぼっち・ざ・ろっく!", season=None, episode=7)
    assert align_with_expected(parse, _expected(7)).verdict == "fast_path"


def test_align_conflict_other_series() -> None:
    alignment = align_with_expected(_parse(title="葬送的芙莉莲"), _expected(5))
    assert alignment.verdict == "conflict"


def test_align_conflict_other_season() -> None:
    alignment = align_with_expected(_parse(season=2), _expected(5))
    assert alignment.verdict == "conflict"


def test_align_episode_variant_double_episode() -> None:
    alignment = align_with_expected(_parse(episode=6), _expected(5))
    assert alignment.verdict == "episode_variant"
    assert alignment.parsed_episode == 6


def test_align_episode_variant_season_pack() -> None:
    parse = _parse(episode=None, segment=Segment.SEASON_PACK)
    assert align_with_expected(parse, _expected(5)).verdict == "episode_variant"


def test_align_unparsed() -> None:
    assert align_with_expected(None, _expected(5)).verdict == "unparsed"


def test_align_rss_entry_any_episode_is_fast_path() -> None:
    """RSS 季级对齐：任意集命中都是 fast_path（集号交候选匹配）。"""
    alignment = align_rss_entry(_parse(episode=8), expected_titles=("孤独摇滚",), season_number=1)
    assert alignment.verdict == "fast_path"
    assert alignment.parsed_episode == 8


def test_align_rss_entry_variant_and_conflict() -> None:
    pack = align_rss_entry(
        _parse(episode=None, segment=Segment.SEASON_PACK),
        expected_titles=("孤独摇滚",), season_number=1,
    )
    assert pack.verdict == "episode_variant"
    other = align_rss_entry(
        _parse(title="葬送的芙莉莲", episode=3),
        expected_titles=("孤独摇滚",), season_number=1,
    )
    assert other.verdict == "conflict"


@pytest.mark.parametrize(
    ("parsed", "candidates", "expected"),
    [
        ("Bocchi The Rock!", ("孤独摇滚", "Bocchi the Rock"), True),  # casefold + 标点
        ("孤独摇滚 第二季", ("孤独摇滚",), True),  # 包含
        ("Frieren", ("孤独摇滚",), False),
        ("", ("孤独摇滚",), False),
    ],
)
def test_title_matches(parsed: str, candidates: tuple[str, ...], expected: bool) -> None:
    assert title_matches(parsed, candidates) is expected


# ---------------------------------------------------------------------------
# 错配恢复 A/B/C 决策表（D14，参数化钉死）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    (
        "parse_valid", "title_match", "target_id", "target_state",
        "backfill_used", "budget", "conflict", "branch",
        "quarantine", "reattach", "backfill", "blacklist", "pending",
    ),
    [
        # A 改挂：解析有效 + 同番 + 目标集存在（零重下、不隔离、不拉黑）
        (True, True, 12, "missing", 0, 2, False, "A_reattach",
         False, 12, False, False, False),
        # C 回补：同番但目标集不存在（回 MISSING + 立即回补 + 拉黑该 hash）
        (True, True, None, None, 0, 2, False, "C_backfill",
         True, None, True, True, False),
        # C 回补：目标集已被 ORGANIZED 占用（内容撞车 → 文件不可救）
        (True, True, 5, "organized", 1, 2, False, "C_backfill",
         True, None, True, True, False),
        # 预算用尽：转人工 + 拉黑（防错标源霸榜死循环烧流量）
        (True, True, None, None, 2, 2, False, "C_budget_exhausted",
         True, None, False, True, True),
        (True, True, None, None, 3, 2, False, "C_budget_exhausted",
         True, None, False, True, True),
        # B 人工：解析失败
        (False, False, None, None, 0, 2, False, "B_manual",
         True, None, False, False, True),
        # B 人工：陌生番（title 不命中）
        (True, False, 9, "missing", 0, 2, False, "B_manual",
         True, None, False, False, True),
        # B 人工：证据矛盾
        (True, True, 7, "missing", 0, 2, True, "B_manual",
         True, None, False, False, True),
    ],
)
def test_decide_mismatch_table(
    parse_valid: bool,
    title_match: bool,
    target_id: int | None,
    target_state: str | None,
    backfill_used: int,
    budget: int,
    conflict: bool,
    branch: str,
    quarantine: bool,
    reattach: int | None,
    backfill: bool,
    blacklist: bool,
    pending: bool,
) -> None:
    decision = decide_mismatch(
        MismatchEvidence(
            parse_valid=parse_valid,
            title_match=title_match,
            target_episode_id=target_id,
            target_episode_state=target_state,
            backfill_used=backfill_used,
            budget=budget,
            evidence_conflict=conflict,
        )
    )
    assert decision.branch == branch
    assert decision.quarantine is quarantine
    assert decision.reattach_episode_id == reattach
    assert decision.backfill is backfill
    assert decision.blacklist_hash is blacklist
    assert decision.to_pending_queue is pending
