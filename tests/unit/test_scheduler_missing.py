"""scheduler.missing 单测（E4a）：缺集 diff 纯函数 + D20 JST 判定。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from autoanime.core.enums import EpisodeState
from autoanime.scheduler.missing import EpisodeFact, season_gap, today_jst


def _fact(number: int, state: EpisodeState, air_date: date | None) -> EpisodeFact:
    return EpisodeFact(number=number, state=state, air_date=air_date)


def test_gap_counts_aired_missing_only() -> None:
    facts = [
        _fact(1, EpisodeState.ORGANIZED, date(2026, 8, 1)),
        _fact(2, EpisodeState.MISSING, date(2026, 8, 8)),  # 缺口
        _fact(3, EpisodeState.MISSING, date(2026, 9, 30)),  # 未放送
    ]
    gap = season_gap(facts, today=date(2026, 9, 6), season_id=7)
    assert gap.season_id == 7
    assert gap.released_progress == 2
    assert gap.aired_missing == (2,)
    assert gap.not_yet_aired == (3,)
    assert gap.has_gap is True
    assert gap.complete is False


def test_gap_complete_when_all_aired_organized() -> None:
    facts = [
        _fact(1, EpisodeState.ORGANIZED, date(2026, 8, 1)),
        _fact(2, EpisodeState.UPGRADED, date(2026, 8, 8)),
        _fact(3, EpisodeState.ORGANIZED, date(2026, 8, 15)),
    ]
    gap = season_gap(facts, today=date(2026, 9, 6))
    assert gap.complete is True
    assert gap.has_gap is False


def test_gap_ignores_and_flagged_do_not_block_collect() -> None:
    facts = [
        _fact(1, EpisodeState.ORGANIZED, date(2026, 8, 1)),
        _fact(2, EpisodeState.IGNORED, date(2026, 8, 8)),
    ]
    gap = season_gap(facts, today=date(2026, 9, 6))
    assert gap.complete is True


def test_gap_unknown_air_date_never_completes() -> None:
    facts = [
        _fact(1, EpisodeState.ORGANIZED, date(2026, 8, 1)),
        _fact(2, EpisodeState.ORGANIZED, None),
    ]
    gap = season_gap(facts, today=date(2026, 9, 6))
    assert gap.complete is False


def test_today_jst_handles_utc_evening_as_next_day() -> None:
    # UTC 2026-09-06 15:00 = JST 2026-09-07 00:00（日本凌晨放送番的 D20 边界）
    utc_evening = datetime(2026, 9, 6, 15, 0, tzinfo=UTC)
    assert today_jst(utc_evening) == date(2026, 9, 7)


def test_today_jst_handles_naive_local_time() -> None:
    naive = datetime.now() - timedelta(days=1)
    assert today_jst(naive).weekday() in range(7)  # 可计算即达意：无时区假设崩溃
