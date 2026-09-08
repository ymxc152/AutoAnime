"""库收纳（P0-B）：import/confirm 归档文件的库条目 upsert。

根因（新手可用性调研）：``Series`` 行只在订阅时创建，import 归档只搬文件
+ audit 不建任何 DB 行 → Library 页对 qb+导入用户永远为空。本模块在归档
落盘后把 series/season/episode 树补进库：

- 查重：``Series.bangumi_id`` 精确匹配优先（import 路径暂无，为订阅
  adopt 预留）；否则 ``build_title_shape`` 归一化标题与全量 Series 行
  内存比对（单用户库量小，全量可接受）。``media_type`` 参与比对——
  剧场版与 TV 同题不合并（同题异版风险，见计划 §风险 3）。
- find_or_create：Series（status="active"）→ Season（series_id+number
  查重）→ Episode。
- Episode 直接以 ``state=ORGANIZED`` **插入**（insert 非状态机转移，
  零破坏）；``file_path`` 落真实归档路径。
- 集号语义：``episode_number=None`` 且 media_type=movie → season 1 /
  number 1；season pack 解不出集号 → number=0（已知 tradeoff：该行无
  air_date 使该季 complete 永假，仅影响 COLLECTED 降频，不影响缺口）。
- 幂等：同 series+season+number 已存在 ORGANIZED/UPGRADED/MISSING 行 →
  file_path 相同则 no-op；不同且新文件在盘上则更新指针（经
  ``update_episode_archive_state``，MISSING→ORGANIZED / UPGRADED→
  ORGANIZED 均为合法转移，状态机零破坏）；其余状态不动。
- 任何异常吞掉记 ``logging.warning`` 返回失败 report，绝不抛出拖垮导入批。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from autoanime.core.enums import EpisodeState, MediaType, SeasonState
from autoanime.core.models import Episode, Season, Series
from autoanime.pipeline.l2.placeholders import build_title_shape
from autoanime.scheduler.store import LoopStore

logger = logging.getLogger(__name__)

_TITLE_SLOTS = ("title_cn", "title_jp", "title_romaji")


@dataclass
class IngestReport:
    """一次 upsert 的小计（调用方可记日志/明细；失败不抛出）。"""

    ok: bool
    series_id: int | None = None
    season_id: int | None = None
    episode_id: int | None = None
    created_series: bool = False
    created_season: bool = False
    created_episode: bool = False
    updated_file: bool = False
    note: str | None = None


def _enum_value(value: MediaType | EpisodeState | SeasonState | str) -> str:
    return (
        value.value
        if isinstance(value, MediaType | EpisodeState | SeasonState)
        else str(value)
    )


def _title_shapes(row: Series) -> set[str]:
    """一个 Series 行全部非空标题的归一化 shape 集合。"""
    return {
        build_title_shape(title)
        for title in (row.title_cn, row.title_jp, row.title_romaji)
        if title
    }


async def upsert_archived_file(
    store: LoopStore,
    *,
    titles: dict[str, str | None],
    media_type: str | MediaType,
    season_number: int | None,
    episode_number: int | None,
    file_path: str,
    quality_score: float | None = None,
    bangumi_id: str | None = None,
) -> IngestReport:
    """归档文件落库（P0-B 库收纳）：series/season/episode 树 upsert。

    ``titles`` 为 dict 形态（title_cn/title_jp/title_romaji，能拿到的传）；
    import 路径通常只有 ``title_cn``（解析结论标题）。失败吞异常返回
    ``ok=False`` 的 report。
    """
    try:
        return await _upsert(
            store,
            titles=titles,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
            file_path=file_path,
            quality_score=quality_score,
            bangumi_id=bangumi_id,
        )
    except Exception:  # noqa: BLE001 -- 收纳失败绝不拖垮导入批
        logger.warning("library ingest failed for %s", file_path, exc_info=True)
        return IngestReport(ok=False, note="ingest-error")


async def _upsert(
    store: LoopStore,
    *,
    titles: dict[str, str | None],
    media_type: str | MediaType,
    season_number: int | None,
    episode_number: int | None,
    file_path: str,
    quality_score: float | None,
    bangumi_id: str | None,
) -> IngestReport:
    media = MediaType(_enum_value(media_type))
    slots: dict[str, str] = {}
    for slot in _TITLE_SLOTS:
        value = titles.get(slot)
        if value:
            slots[slot] = value
    incoming_shapes = {build_title_shape(title) for title in slots.values()}
    if not incoming_shapes and not bangumi_id:
        # ck_series_title 约束要求至少一个标题；无标题也无 bangumi_id 无法建行
        return IngestReport(ok=False, note="no-usable-title")

    # --- find_or_create Series（bangumi_id 精确优先，title shape 次之） ------
    rows = await store.list_series()
    series: Series | None = None
    created_series = False
    if bangumi_id:
        series = next((row for row in rows if row.bangumi_id == bangumi_id), None)
    if series is None and incoming_shapes:
        series = next(
            (
                row
                for row in rows
                if _enum_value(row.media_type) == media.value
                and _title_shapes(row) & incoming_shapes
            ),
            None,
        )
    if series is None:
        series = Series(
            title_cn=slots.get("title_cn"),
            title_jp=slots.get("title_jp"),
            title_romaji=slots.get("title_romaji"),
            media_type=media,
            bangumi_id=bangumi_id,
            status="active",
        )
        series = await store.insert_series(series)
        created_series = True

    # --- find_or_create Season（series_id + number 查重） ---------------------
    season_no = season_number if season_number is not None else 1
    # 集号语义（P0-B）：剧场版无集号 → season 1 / number 1；season pack 解不
    # 出集号 → number=0（tradeoff：无 air_date 使该季 complete 永假，仅影响
    # COLLECTED 降频）。
    number = episode_number if episode_number is not None else (
        1 if media is MediaType.MOVIE else 0
    )
    seasons = await store.seasons_for_series(series.id)
    season: Season | None = next((s for s in seasons if s.number == season_no), None)
    created_season = False
    if season is None:
        season = Season(
            series_id=series.id, number=season_no, status=SeasonState.UPCOMING
        )
        season = await store.insert_season(season)
        created_season = True

    # --- Episode：直接 INSERT ORGANIZED；已存在行走幂等分支 -------------------
    existing = await store.episode_for_number(season.id, number)
    if existing is None:
        row = Episode(
            series_id=series.id,
            season_id=season.id,
            number=number,
            state=EpisodeState.ORGANIZED,
            file_path=file_path,
            quality_score=quality_score,
        )
        row = await store.insert_episode(row)
        return IngestReport(
            ok=True,
            series_id=series.id,
            season_id=season.id,
            episode_id=row.id,
            created_series=created_series,
            created_season=created_season,
            created_episode=True,
        )

    # 幂等：同 series+season+number 已有行——file_path 相同 no-op；不同且新
    # 文件在盘上才更新（MISSING→ORGANIZED / UPGRADED→ORGANIZED 均合法转移）。
    state = EpisodeState(_enum_value(existing.state))
    if state not in (EpisodeState.ORGANIZED, EpisodeState.UPGRADED, EpisodeState.MISSING):
        return IngestReport(
            ok=True,
            series_id=series.id,
            season_id=season.id,
            episode_id=existing.id,
            created_series=created_series,
            created_season=created_season,
            note=f"episode {number} in state {state.value}; untouched",
        )
    if existing.file_path == file_path:
        return IngestReport(
            ok=True,
            series_id=series.id,
            season_id=season.id,
            episode_id=existing.id,
            created_series=created_series,
            created_season=created_season,
        )
    if not Path(file_path).exists():
        return IngestReport(
            ok=True,
            series_id=series.id,
            season_id=season.id,
            episode_id=existing.id,
            created_series=created_series,
            created_season=created_season,
            note="archived file missing on disk; kept current pointer",
        )
    await store.update_episode_archive_state(
        existing.id,
        target=EpisodeState.ORGANIZED,
        file_path=file_path,
        quality_score=quality_score,
    )
    return IngestReport(
        ok=True,
        series_id=series.id,
        season_id=season.id,
        episode_id=existing.id,
        created_series=created_series,
        created_season=created_season,
        updated_file=True,
    )
