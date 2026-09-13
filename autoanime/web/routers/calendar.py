"""Bangumi 选番只读端点（P1-C）：/api/season-calendar 与 /api/season-browse。

外呼全部经 app.state.bangumi_calendar（lifespan 装配的
``BangumiCalendarGateway``，带 TTL 缓存与降级链）；本层零业务逻辑，
失败不抛 500——calendar 拉取失败返回 ``degraded: true`` 空表。audit 只记
「查询了当季/历史季」的年份/季名，不记任何外链 URL。token 中间件
（app.py 统一装配）自动覆盖本组端点，无需额外处理。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from autoanime.gateway.bangumi_calendar import (
    BangumiFetchError,
    BangumiItem,
    SeasonBrowseResult,
)
from autoanime.web.deps import BangumiCalendarDep, GovernanceDep

router = APIRouter(tags=["calendar"])

SeasonName = Literal["winter", "spring", "summer", "fall"]
"""季名枚举：非法值由 FastAPI 校验层直接 422。"""


class BangumiItemOut(BaseModel):
    """规范化番剧条目（schemas.py 外的另一组只读出参模型）。"""

    subject_id: int
    title_cn: str | None
    title_jp: str
    image_url: str | None
    rating: float | None
    air_date: str | None
    eps: int | None
    mikan_search_url: str
    platform: str | None
    region: str | None


class SeasonBrowseOut(BaseModel):
    """时间表/季浏览统一出参；``degraded=True`` 时 items 为空并附 reason。"""

    items: list[BangumiItemOut]
    degraded: bool
    reason: str | None


def _out(result: SeasonBrowseResult) -> SeasonBrowseOut:
    return SeasonBrowseOut(
        items=[
            BangumiItemOut(
                subject_id=item.subject_id,
                title_cn=item.title_cn,
                title_jp=item.title_jp,
                image_url=item.image_url,
                rating=item.rating,
                air_date=item.air_date,
                eps=item.eps,
                mikan_search_url=item.mikan_search_url,
                platform=item.platform,
                region=item.region,
            )
            for item in result.items
        ],
        degraded=result.degraded,
        reason=result.reason,
    )


def _items_result(items: tuple[BangumiItem, ...]) -> SeasonBrowseResult:
    return SeasonBrowseResult(items=items, degraded=False, reason=None)


@router.get("/season-calendar", response_model=SeasonBrowseOut)
async def season_calendar(
    gateway: BangumiCalendarDep, governance: GovernanceDep
) -> SeasonBrowseOut:
    """每日放送时间表（网关缓存 6h；拉取失败降级为空表，不 500）。"""
    try:
        out = _out(_items_result(await gateway.fetch_calendar()))
    except BangumiFetchError as exc:
        out = SeasonBrowseOut(items=[], degraded=True, reason=exc.detail)
    await governance.record_audit(
        operation_id=uuid4().hex,
        entity="season_calendar",
        action="season_calendar_viewed",
        instruction={"scope": "daily"},
    )
    return out


@router.get("/season-browse", response_model=SeasonBrowseOut)
async def season_browse(
    gateway: BangumiCalendarDep,
    governance: GovernanceDep,
    year: Annotated[int, Query(ge=1990, description="开播年份")],
    season: SeasonName,
) -> SeasonBrowseOut:
    """按季浏览（winter/spring/summer/fall；year 上限 = 当前年 + 1）。"""
    max_year = datetime.now(UTC).year + 1
    if year > max_year:
        raise HTTPException(
            status_code=422, detail=f"year must be <= {max_year}"
        )
    result = await gateway.fetch_season(year, season)
    await governance.record_audit(
        operation_id=uuid4().hex,
        entity="season_calendar",
        action="season_browse_viewed",
        instruction={"year": year, "season": season},
    )
    return _out(result)
