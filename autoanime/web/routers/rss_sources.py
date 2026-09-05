"""RSS 源管理（/api/rss_sources，B3）：token 不回显，挂 season。"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException

from autoanime.core.events import EventCategory
from autoanime.core.models import RssSource
from autoanime.web.deps import ApiStoreDep, BusDep, GovernanceDep, PaginationDep
from autoanime.web.learning import publish
from autoanime.web.schemas import Page, RssSourceCreateIn, RssSourceOut, RssSourceUpdateIn

router = APIRouter(prefix="/rss_sources", tags=["rss-sources"])


def _source_out(row: RssSource) -> RssSourceOut:
    return RssSourceOut(
        id=row.id,
        url=row.url,
        has_token=row.token is not None,
        season_id=row.season_id,
        enabled=row.enabled,
        last_polled_at=row.last_polled_at,
    )


@router.get("", response_model=Page[RssSourceOut])
async def list_rss_sources(
    store: ApiStoreDep, pagination: PaginationDep
) -> Page[RssSourceOut]:
    rows, total = await store.list_rss_sources_page(
        limit=pagination.limit, offset=pagination.offset
    )
    return Page(
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
        items=[_source_out(row) for row in rows],
    )


@router.post("", response_model=RssSourceOut, status_code=201)
async def create_rss_source(
    body: RssSourceCreateIn,
    store: ApiStoreDep,
    governance: GovernanceDep,
    bus: BusDep,
) -> RssSourceOut:
    if not await store.season_exists(body.season_id):
        raise HTTPException(
            status_code=404, detail=f"season {body.season_id} not found"
        )
    row = RssSource(
        url=body.url,
        token=body.token.get_secret_value() if body.token is not None else None,
        season_id=body.season_id,
        enabled=body.enabled,
    )
    saved = await store.add_rss_source(row)
    audit = await governance.record_audit(
        operation_id=uuid4().hex,
        entity="rss_sources",
        entity_id=saved.id,
        action="rss_source_created",
        instruction={"season_id": saved.season_id, "enabled": saved.enabled},
    )
    await publish(
        bus,
        category=EventCategory.SYSTEM,
        message="rss_source.created",
        audit_id=audit.id,
        rss_source_id=saved.id,
    )
    return _source_out(saved)


@router.patch("/{source_id}", response_model=RssSourceOut)
async def update_rss_source(
    source_id: int,
    body: RssSourceUpdateIn,
    store: ApiStoreDep,
    governance: GovernanceDep,
    bus: BusDep,
) -> RssSourceOut:
    supplied = body.model_dump(exclude_unset=True)
    if not supplied:
        raise HTTPException(status_code=422, detail="no updatable fields supplied")
    fields: dict[str, object] = {}
    if "url" in supplied:
        fields["url"] = supplied["url"]
    if "enabled" in supplied:
        fields["enabled"] = supplied["enabled"]
    if "token" in supplied:
        token_secret = supplied["token"]
        # 显式传 null = 清除 token；传值 = 更新。
        fields["token"] = None if token_secret is None else str(token_secret)
    updated = await store.update_rss_source(source_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"rss source {source_id} not found")
    audit = await governance.record_audit(
        operation_id=uuid4().hex,
        entity="rss_sources",
        entity_id=source_id,
        action="rss_source_updated",
        instruction={key: ("***" if key == "token" else value) for key, value in fields.items()},
    )
    await publish(
        bus,
        category=EventCategory.SYSTEM,
        message="rss_source.updated",
        audit_id=audit.id,
        rss_source_id=source_id,
    )
    return _source_out(updated)


@router.delete("/{source_id}", status_code=204)
async def delete_rss_source(
    source_id: int,
    store: ApiStoreDep,
    governance: GovernanceDep,
    bus: BusDep,
) -> None:
    deleted = await store.delete_rss_source(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"rss source {source_id} not found")
    audit = await governance.record_audit(
        operation_id=uuid4().hex,
        entity="rss_sources",
        entity_id=source_id,
        action="rss_source_deleted",
        instruction={},
    )
    await publish(
        bus,
        category=EventCategory.SYSTEM,
        message="rss_source.deleted",
        audit_id=audit.id,
        rss_source_id=source_id,
    )
