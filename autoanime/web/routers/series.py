"""Library 页（/api/series）：series 列表 + season/episode 树 + 海报（只读）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from autoanime.core.models import Episode, Season, Series
from autoanime.organize.naming import sanitize
from autoanime.web.deps import ApiStoreDep, PaginationDep, SettingsDep
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
    title_cn → title_romaji → title_jp 依次尝试。目录名来自 DB 标题并经
    sanitize 清洗，不存在路径穿越面（series_id 是 int，目录不含分隔符）。
    """
    folders: list[str] = []
    for title in (series.title_cn, series.title_romaji, series.title_jp):
        if not title:
            continue
        cleaned = sanitize(title)
        if cleaned != "Unknown" and cleaned not in folders:
            folders.append(cleaned)
    return [
        library_path / folder / f"poster{ext}"
        for folder in folders
        for ext in _POSTER_EXTENSIONS
    ]


@router.get("/{series_id}/poster")
async def get_series_poster(series_id: int, store: ApiStoreDep, settings: SettingsDep) -> FileResponse:
    """系列海报：本地整理库文件优先，缺失时 404（前端降级为文字卡片）。

    TODO(poster_url)：参考源元数据层落地 poster_url 字段后，在此于 404 前
    返回重定向/代理，作为本地文件的兜底（PR3 决策：本地优先 → 后端字段）。
    """
    row = await store.get_series(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"series {series_id} not found")
    for candidate in _poster_candidates(row, settings.library_path):
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
