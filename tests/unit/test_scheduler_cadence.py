"""scheduler.cadence 单测（E4a）：抖动纯函数 + 按季状态降频（注入 now）。"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from autoanime.core.enums import SeasonState
from autoanime.scheduler.cadence import jittered_interval_seconds, should_poll_season

NOW = datetime(2026, 9, 6, 12, 0, 0)


def test_jittered_interval_stays_within_bounds() -> None:
    rng = random.Random(42)
    for _ in range(200):
        seconds = jittered_interval_seconds(30, 10, rng)
        assert 30 * 60 * 0.9 <= seconds <= 30 * 60 * 1.1


def test_jittered_interval_zero_pct_is_exact() -> None:
    rng = random.Random(1)
    assert jittered_interval_seconds(30, 0, rng) == 1800.0


def test_jittered_interval_nonpositive_base_is_zero() -> None:
    assert jittered_interval_seconds(0, 10, random.Random(1)) == 0.0


def test_airing_polls_every_round() -> None:
    assert should_poll_season(
        season_status=SeasonState.AIRING, last_polled_at=NOW - timedelta(minutes=5), now=NOW
    )


def test_collected_throttles_to_monthly() -> None:
    recent = should_poll_season(
        season_status=SeasonState.COLLECTED,
        last_polled_at=NOW - timedelta(days=10),
        now=NOW,
        collected_days=30,
    )
    due = should_poll_season(
        season_status=SeasonState.COLLECTED,
        last_polled_at=NOW - timedelta(days=30),
        now=NOW,
        collected_days=30,
    )
    never_polled = should_poll_season(
        season_status=SeasonState.COLLECTED, last_polled_at=None, now=NOW
    )
    assert recent is False
    assert due is True
    assert never_polled is True


def test_ended_and_upcoming_never_poll() -> None:
    for status in (SeasonState.ENDED, SeasonState.UPCOMING):
        assert not should_poll_season(season_status=status, last_polled_at=None, now=NOW)
