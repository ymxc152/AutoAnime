"""缺集检测（E4）：放送进度 diff 的纯函数（D20 JST 纪律）。

ARCHITECTURE §2：轮询后 diff [放送进度期望集数] vs [非 MISSING 集]，
缺口列表触发缺口报告 + 通知（D15：v1 回补 = 等 RSS 自然命中，不主动搜索）。
判定一律用 JST 的「今天」（防日本凌晨放送番的假缺口），展示层转本地时区。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timezone
from zoneinfo import ZoneInfo

from autoanime.core.enums import EpisodeState

JST = ZoneInfo("Asia/Tokyo")

#: 参与集齐判定的状态：这些之外的（IGNORED/FLAGGED）不挡 COLLECTED。
_BLOCKING_STATES = frozenset({EpisodeState.MISSING, EpisodeState.DOWNLOADING, EpisodeState.DOWNLOADED})


def today_jst(now: datetime) -> date:
    """把任意时刻换算成 JST 日历日（D20：判定一律 JST）。"""
    if now.tzinfo is None:
        # 库内 datetime 均为本地 naive：锚定本地 UTC 偏移再换 JST。
        local_offset = now.astimezone().utcoffset()
        now = now.replace(tzinfo=timezone(local_offset) if local_offset else UTC)
    return now.astimezone(JST).date()


@dataclass(frozen=True)
class EpisodeFact:
    """缺集 diff 的最小输入（与 Episode 行字段对应，纯函数不碰 ORM）。"""

    number: int
    state: EpisodeState
    air_date: date | None


@dataclass(frozen=True)
class SeasonGap:
    """一季的缺口报告（纯数据，通知/CLI/报表共用）。"""

    season_id: int
    episodes_total: int
    released_progress: int
    aired_missing: tuple[int, ...]
    not_yet_aired: tuple[int, ...]
    complete: bool

    @property
    def has_gap(self) -> bool:
        return bool(self.aired_missing)


def season_gap(
    facts: list[EpisodeFact],
    *,
    today: date,
    season_id: int = 0,
) -> SeasonGap:
    """按 JST 今日放送进度对一季做缺集 diff。

    - ``released_progress``：air_date ≤ today（JST）的集数（放送进度期望）；
    - ``aired_missing``：已放送但仍 MISSING 的集号（缺口 = 缺集回补名单）；
    - ``complete``：无缺口且放送进度追平总数（「集齐」判定，COLLECTED 降频
      的依据）；air_date 未知的集不参与放送进度，也判不了缺口——按 v1
      「API 无集表不硬拦截」的纪律（ARCHITECTURE 5.6），只有当全部集都有
      air_date 时才可能 complete。
    """
    episodes_total = len(facts)
    released_progress = 0
    aired_missing: list[int] = []
    not_yet_aired: list[int] = []
    all_air_dates_known = True
    for fact in sorted(facts, key=lambda f: f.number):
        if fact.air_date is None:
            all_air_dates_known = False
            if fact.state is EpisodeState.MISSING:
                not_yet_aired.append(fact.number)
            continue
        if fact.air_date > today:
            not_yet_aired.append(fact.number)
            continue
        released_progress += 1
        if fact.state is EpisodeState.MISSING:
            aired_missing.append(fact.number)
    complete = (
        all_air_dates_known
        and not aired_missing
        and released_progress == episodes_total
        and all(fact.state not in _BLOCKING_STATES for fact in facts)
    )
    return SeasonGap(
        season_id=season_id,
        episodes_total=episodes_total,
        released_progress=released_progress,
        aired_missing=tuple(aired_missing),
        not_yet_aired=tuple(not_yet_aired),
        complete=complete,
    )
