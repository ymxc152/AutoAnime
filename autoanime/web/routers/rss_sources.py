"""RSS 源管理（/api/rss_sources，B3）：token 不回显，挂 season。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from autoanime.core.events import EventCategory
from autoanime.core.models import RssSource
from autoanime.scheduler.scheduler import build_loop
from autoanime.web.deps import (
    ApiStoreDep,
    BusDep,
    GovernanceDep,
    PaginationDep,
    SettingsDep,
    StorageDep,
)
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


@router.post("/{source_id}/poll")
async def poll_rss_source(
    source_id: int,
    request: Request,
    store: ApiStoreDep,
    storage: StorageDep,
    settings: SettingsDep,
    governance: GovernanceDep,
    bus: BusDep,
) -> dict[str, object]:
    """单源立即轮询（12-F）：CLI ``rerun --source-id`` 的 WebUI 版。

    语义与 CLI rerun 完全一致（同一批 store/scheduler 入口）：启动补扫
    （悬挂任务对账）→ 下载比对（推送下载器）→ 只轮询该 RSS 源（cadence
    按季状态降频判定照旧，非到期如实返回 skipped_not_due）。同步快返，
    与 run-once 的处理方式一致；进程内用独立互斥位防并发手动轮询。
    响应返回轮询摘要（新条目/择优推送数等，字段口径同 CLI rerun JSON）。
    """
    source = await store.get_rss_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"rss source {source_id} not found")
    if not source.enabled:
        raise HTTPException(status_code=409, detail=f"rss source {source_id} is disabled")
    if getattr(request.app.state, "rss_source_poll_running", False):
        raise HTTPException(status_code=409, detail="another rss poll is already active")
    request.app.state.rss_source_poll_running = True
    try:
        # 与 scheduler.run-once 同一批组件装配：优先复用 lifespan 持有的
        # loop_components，否则自建并在结束时 close（共享 storage 不关闭）。
        components = getattr(request.app.state, "loop_components", None)
        owns_components = components is None
        if components is None:
            components = build_loop(settings, storage=storage, bus=bus)
        try:
            now = datetime.now(UTC)
            reconcile = await components.download_poller.reconcile_startup(now=now)
            downloads = await components.download_poller.poll_once(now=now)
            outcome = await components.rss_poller.poll_source(source, now=now)
        finally:
            if owns_components:
                await components.close()
        result: dict[str, object] = {
            "source_id": outcome.source_id,
            "season_id": outcome.season_id,
            "skipped_not_due": outcome.skipped_not_due,
            "fetch_error": outcome.fetch_error,
            "entries_total": outcome.entries_total,
            "seen": outcome.seen,
            "rejected": outcome.rejected,
            "backlog": outcome.backlog,
            "picked": outcome.picked,
            "gaps": list(outcome.gaps),
            "reconciled": reconcile.reconciled,
            "reconcile_notes": list(reconcile.notes),
            "download": {
                "checked": downloads.checked,
                "completed": downloads.completed,
                "failed": downloads.failed,
                "retried": downloads.retried,
                "notes": list(downloads.notes),
            },
        }
        audit = await governance.record_audit(
            operation_id=uuid4().hex,
            entity="rss_sources",
            entity_id=source_id,
            action="rss_source_polled",
            instruction={
                "skipped_not_due": outcome.skipped_not_due,
                "fetch_error": outcome.fetch_error,
                "entries_total": outcome.entries_total,
                "seen": outcome.seen,
                "rejected": outcome.rejected,
                "backlog": outcome.backlog,
                "picked": outcome.picked,
            },
        )
        await publish(
            bus,
            category=EventCategory.SYSTEM,
            message="rss_source.polled",
            audit_id=audit.id,
            source_id=source_id,
            picked=outcome.picked,
        )
        return result
    finally:
        request.app.state.rss_source_poll_running = False
