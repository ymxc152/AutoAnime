"""Library 页（/api/series）：series 列表 + season/episode 树 + 海报（只读）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from autoanime.core.models import Episode, Season, Series
from autoanime.organize.poster import poster_folders
from autoanime.web.deps import (
    ApiStoreDep,
    PaginationDep,
    PosterServiceDep,
    ReferenceChainDep,
    SettingsDep,
)
from autoanime.web.schemas import EpisodeOut, Page, SeasonOut, SeriesOut

router = APIRouter(prefix="/series", tags=["library"])


def _tree_for(series_rows: list[Series], seasons: list[Season], episodes: list[Episode]) -> list[SeriesOut]:
    episodes_by_series: dict[int, list[EpisodeOut]] = {}
    for episode in episodes:
        episodes_by_series.setdefault(episode.series_id, []).append(EpisodeOut.model_validate(episode))
    seasons_by_series: dict[int, list[SeasonOut]] = {}
    for season in seasons:
        seasons_by_series.setdefault(season.series_id, []).append(
            SeasonOut(
                id=season.id,
                series_id=season.series_id,
                number=season.number,
                status=str(season.status.value if hasattr(season.status, "value") else season.status),
                episodes=episodes_by_series.get(season.id, []),
            )
        )
    return [
        SeriesOut(
            id=row.id,
            title_cn=row.title_cn,
            title_jp=row.title_jp,
            title_romaji=row.title_romaji,
            media_type=str(row.media_type.value if hasattr(row.media_type, "value") else row.media_type),
            tmdb_id=row.tmdb_id,
            bangumi_id=row.bangumi_id,
            fansub_pref=row.fansub_pref,
            quality_pref=row.quality_pref,
            status=row.status,
            seasons=seasons_by_series.get(row.id, []),
        )
        for row in series_rows
    ]


async def _build_tree(store: ApiStoreDep, series_rows: list[Series]) -> list[SeriesOut]:
    ids = [row.id for row in series_rows]
    seasons = await store.seasons_for(ids)
    episodes = await store.episodes_for(ids)
    return _tree_for(series_rows, seasons, episodes)


@router.get("", response_model=Page[SeriesOut])
async def list_series(store: ApiStoreDep, pagination: PaginationDep) -> Page[SeriesOut]:
    rows, total = await store.list_series_page(pagination.limit, pagination.offset)
    items = await _build_tree(store, rows)
    return Page(total=total, limit=pagination.limit, offset=pagination.offset, items=items)


_POSTER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def _poster_candidates(series: Series, library_path: Path) -> list[Path]:
    """本地库海报候选：{library_path}/{标题目录}/poster.{ext}。

    目录名与整理命名（naming.sanitize + display_title）一致，按
    title_cn → title_romaji → title_jp 依次尝试（目录推导复用
    ``organize.poster.poster_folders`` 单一事实源，与兜底下载同源防漂移）。
    目录名来自 DB 标题并经 sanitize 清洗，不存在路径穿越面（series_id
    是 int，目录不含分隔符）。
    """
    return [
        library_path / folder / f"poster{ext}"
        for folder in poster_folders((series.title_cn, series.title_romaji, series.title_jp))
        for ext in _POSTER_EXTENSIONS
    ]


@router.get("/{series_id}/poster")
async def get_series_poster(
    series_id: int,
    store: ApiStoreDep,
    settings: SettingsDep,
    reference_chain: ReferenceChainDep,
    poster_service: PosterServiceDep,
) -> FileResponse:
    """系列海报：本地整理库文件优先，缺失时参考源兜底下载（PR3+）。

    本地 ``{library}/{标题目录}/poster.{ext}`` 命中直读；缺失且海报下载
    开启时懒拉取（链查询 → 下载 → 落盘 → 返回），失败/冷却期内/开关关闭
    保持 404（前端降级为文字卡片）。全程 best-effort：兜底路径任何异常
    都不会以 5xx 形态外泄（service 内部已兜底，这里只做 404 收口）。
    """
    row = await store.get_series(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"series {series_id} not found")
    candidates = _poster_candidates(row, settings.library_path)
    for candidate in candidates:
        if candidate.is_file():
            return FileResponse(candidate)
    if candidates and poster_service.enabled and reference_chain is not None:
        ext = await poster_service.ensure_poster(
            titles=(row.title_cn, row.title_romaji, row.title_jp),
            library_path=Path(settings.library_path),
        )
        if ext is not None:
            for candidate in candidates:
                if candidate.is_file():
                    return FileResponse(candidate)
    raise HTTPException(status_code=404, detail=f"series {series_id} poster not found")


@router.get("/{series_id}", response_model=SeriesOut)
async def get_series(series_id: int, store: ApiStoreDep) -> SeriesOut:
    row = await store.get_series(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"series {series_id} not found")
    items = await _build_tree(store, [row])
    return items[0]
