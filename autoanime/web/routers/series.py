"""Library 页（/api/series）：series 列表 + season/episode 树（只读）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from autoanime.core.models import Episode, Season, Series
from autoanime.web.deps import ApiStoreDep, PaginationDep
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


@router.get("/{series_id}", response_model=SeriesOut)
async def get_series(series_id: int, store: ApiStoreDep) -> SeriesOut:
    row = await store.get_series(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"series {series_id} not found")
    items = await _build_tree(store, [row])
    return items[0]
