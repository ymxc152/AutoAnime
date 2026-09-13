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
            include_keywords=row.include_keywords,
            exclude_keywords=row.exclude_keywords,
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
    rows, total = await store.list_series_page(limit=pagination.limit, offset=pagination.offset)
    items = await _subscription_out(store, rows)
    return Page(total=total, limit=pagination.limit, offset=pagination.offset, items=items)


@router.post("", response_model=SubscriptionOut, status_code=201)
async def create_subscription(
    body: SubscriptionCreateIn,
    store: ApiStoreDep,
    governance: GovernanceDep,
    bus: BusDep,
) -> SubscriptionOut:
    """新建订阅：Series + 当季 Season + 预生成 N 条 MISSING 集行（一个事务）。

    P0-B adopt：bangumi_id（精确）/ 标题 shape 命中已有 Series 时收编——
    置回 active、只补缺失集号 MISSING（import 归档的 ORGANIZED 行保留，
    消「先导入后订阅整季幻影 MISSING」）。rss_url 提供时同一事务挂
    RssSource（token 只落库不回显，响应只给 rss_saved 布尔）。
    """
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
        bangumi_id=body.bangumi_id,
        fansub_pref=body.fansub_pref,
        quality_pref=body.quality_pref,
        include_keywords=body.include_keywords,
        exclude_keywords=body.exclude_keywords,
        status="active",
    )
    try:
        upserted = await store.create_or_adopt_subscription(
            series,
            season,
            episodes,
            rss_url=str(body.rss_url) if body.rss_url else None,
            rss_token=(
                body.rss_token.get_secret_value() if body.rss_token is not None else None
            ),
        )
    except Exception as exc:  # 含 ck_series_title 校验失败
        raise HTTPException(status_code=422, detail=f"subscription rejected: {exc}") from None
    created = upserted.series
    audit = await governance.record_audit(
        operation_id=uuid4().hex,
        entity="series",
        entity_id=created.id,
        action="subscription_created",
        instruction={
            "season_number": body.season_number,
            "episodes_pregenerated": len(episodes),
            "media_type": body.media_type,
            "adopted": upserted.adopted,
            "rss_saved": upserted.rss_saved,
        },
    )
    await publish(
        bus,
        category=EventCategory.SYSTEM,
        message="subscription.created",
        audit_id=audit.id,
        series_id=created.id,
    )
    out = await _get_subscription(store, created.id)
    out.rss_saved = upserted.rss_saved
    out.adopted = upserted.adopted
    return out


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
    # exclude_unset:显式 null 表示清除 fansub/quality 偏好；未提供的字段保持不变。
    fields: dict[str, object] = body.model_dump(exclude_unset=True)
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
