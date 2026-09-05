"""订阅管理（/api/subscriptions）：落 series/season/episode 表（ARCHITECTURE §2）。

v1 边界：调度状态（schedule_state/AIRING 降频）随 E4 落地；订阅的持久
载体在 v1 即 series 行（status=active）+ 预生成的季/集行，本端点只做
CRUD 与预生成集表，不做调度。
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException

from autoanime.core.enums import EpisodeState, MediaType
from autoanime.core.events import EventCategory
from autoanime.core.models import Episode, Season, Series
from autoanime.web.deps import ApiStoreDep, BusDep, GovernanceDep, PaginationDep
from autoanime.web.learning import publish
from autoanime.web.queries import ApiStore
from autoanime.web.schemas import (
    Page,
    SubscriptionCreateIn,
    SubscriptionOut,
    SubscriptionUpdateIn,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


async def _subscription_out(store: ApiStore, rows: list[Series]) -> list[SubscriptionOut]:
    ids = [row.id for row in rows]
    seasons = await store.seasons_for(ids)
    episodes = await store.episodes_for(ids)
    rss_counts = await store.season_rss_counts([season.id for season in seasons])

    progress_by_series: dict[int, list] = {}
    for season in seasons:
        season_episodes = [ep for ep in episodes if ep.season_id == season.id]
        progress_by_series.setdefault(season.series_id, []).append(
            {
                "season_id": season.id,
                "number": season.number,
                "status": str(
                    season.status.value
                    if hasattr(season.status, "value")
                    else season.status
                ),
                "episodes_total": len(season_episodes),
                "episodes_missing": sum(
                    1 for ep in season_episodes if ep.state is EpisodeState.MISSING
                ),
                "episodes_organized": sum(
                    1 for ep in season_episodes if ep.state is EpisodeState.ORGANIZED
                ),
                "rss_sources": rss_counts.get(season.id, 0),
            }
        )
    return [
        SubscriptionOut(
            id=row.id,
            title_cn=row.title_cn,
            title_jp=row.title_jp,
            title_romaji=row.title_romaji,
            media_type=str(
                row.media_type.value if hasattr(row.media_type, "value") else row.media_type
            ),
            status=row.status,
            fansub_pref=row.fansub_pref,
            quality_pref=row.quality_pref,
            seasons=progress_by_series.get(row.id, []),
        )
        for row in rows
    ]


async def _get_subscription(store: ApiStore, series_id: int) -> SubscriptionOut:
    row = await store.get_series(series_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"subscription {series_id} not found")
    items = await _subscription_out(store, [row])
    return items[0]


@router.get("", response_model=Page[SubscriptionOut])
async def list_subscriptions(
    store: ApiStoreDep, pagination: PaginationDep
) -> Page[SubscriptionOut]:
    rows, total = await store.list_series_page(pagination.limit, pagination.offset)
    items = await _subscription_out(store, rows)
    return Page(total=total, limit=pagination.limit, offset=pagination.offset, items=items)


@router.post("", response_model=SubscriptionOut, status_code=201)
async def create_subscription(
    body: SubscriptionCreateIn,
    store: ApiStoreDep,
    governance: GovernanceDep,
    bus: BusDep,
) -> SubscriptionOut:
    """新建订阅：Series + 当季 Season + 预生成 N 条 MISSING 集行（一个事务）。"""
    season = Season(number=body.season_number)
    episodes = [
        Episode(number=number, state=EpisodeState.MISSING)
        for number in range(1, (body.episode_count or 0) + 1)
    ]
    series = Series(
        title_cn=body.title_cn,
        title_jp=body.title_jp,
        title_romaji=body.title_romaji,
        media_type=MediaType(body.media_type),
        fansub_pref=body.fansub_pref,
        quality_pref=body.quality_pref,
        status="active",
    )
    try:
        created = await store.create_subscription(series, season, episodes)
    except Exception as exc:  # 含 ck_series_title 校验失败
        raise HTTPException(status_code=422, detail=f"subscription rejected: {exc}") from None
    audit = await governance.record_audit(
        operation_id=uuid4().hex,
        entity="series",
        entity_id=created.id,
        action="subscription_created",
        instruction={
            "season_number": body.season_number,
            "episodes_pregenerated": len(episodes),
            "media_type": body.media_type,
        },
    )
    await publish(
        bus,
        category=EventCategory.SYSTEM,
        message="subscription.created",
        audit_id=audit.id,
        series_id=created.id,
    )
    return await _get_subscription(store, created.id)


@router.get("/{series_id}", response_model=SubscriptionOut)
async def get_subscription(series_id: int, store: ApiStoreDep) -> SubscriptionOut:
    return await _get_subscription(store, series_id)


@router.patch("/{series_id}", response_model=SubscriptionOut)
async def update_subscription(
    series_id: int,
    body: SubscriptionUpdateIn,
    store: ApiStoreDep,
    governance: GovernanceDep,
    bus: BusDep,
) -> SubscriptionOut:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="no updatable fields supplied")
    updated = await store.update_series_fields(series_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"subscription {series_id} not found")
    audit = await governance.record_audit(
        operation_id=uuid4().hex,
        entity="series",
        entity_id=series_id,
        action="subscription_updated",
        instruction=fields,
    )
    await publish(
        bus,
        category=EventCategory.SYSTEM,
        message="subscription.updated",
        audit_id=audit.id,
        series_id=series_id,
    )
    return await _get_subscription(store, series_id)


@router.delete("/{series_id}", status_code=204)
async def delete_subscription(
    series_id: int,
    store: ApiStoreDep,
    governance: GovernanceDep,
    bus: BusDep,
) -> None:
    deleted = await store.delete_subscription(series_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"subscription {series_id} not found")
    audit = await governance.record_audit(
        operation_id=uuid4().hex,
        entity="series",
        entity_id=series_id,
        action="subscription_deleted",
        instruction={},
    )
    await publish(
        bus,
        category=EventCategory.SYSTEM,
        message="subscription.deleted",
        audit_id=audit.id,
        series_id=series_id,
    )
