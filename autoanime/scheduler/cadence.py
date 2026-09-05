"""订阅节奏（E4）：抖动与按季状态降频的纯函数。

- 轮询默认 30min ± 10% 抖动（整点齐射打源站；APScheduler IntervalTrigger
  自带 ``jitter`` 参数承接，本模块的纯函数用于单测钉死与 CLI rerun 汇报）；
- 降频（ARCHITECTURE §1/§2 + D15）：AIRING 每次轮询都到；COLLECTED 只在
  距上次轮询超过 ``collected_days`` 才再查（仅洗版机会检查）；UPCOMING/
  ENDED 不轮询（v1 无主动搜索，ENDED 有缺也等不来种子，如实跳过）。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from autoanime.core.enums import SeasonState

#: 季状态 → 是否参与 RSS 轮询的判定依据（见 should_poll_season）。
_DEFAULT_COLLECTED_DAYS = 30


def jittered_interval_seconds(
    base_minutes: int, jitter_pct: int, rng: random.Random
) -> float:
    """基准间隔（秒）± ``jitter_pct``% 的均匀抖动；非正输入原样返回。"""
    base_seconds = base_minutes * 60
    if base_seconds <= 0 or jitter_pct <= 0:
        return float(max(base_seconds, 0))
    amplitude = base_seconds * jitter_pct / 100
    return float(base_seconds + rng.uniform(-amplitude, amplitude))


def should_poll_season(
    *,
    season_status: SeasonState,
    last_polled_at: datetime | None,
    now: datetime,
    collected_days: int = _DEFAULT_COLLECTED_DAYS,
) -> bool:
    """按季状态判定本轮是否轮询（降频核心，注入 now 单测）。"""
    if season_status is SeasonState.AIRING:
        return True
    if season_status is SeasonState.COLLECTED:
        if last_polled_at is None:
            return True
        return (now - last_polled_at) >= timedelta(days=collected_days)
    return False
