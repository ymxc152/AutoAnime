from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from autoanime.core.events import Event, EventCategory
from autoanime.scheduler.scheduler import build_loop
from autoanime.web.deps import BusDep, SettingsDep, StorageDep

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class SchedulerRunIn(BaseModel):
    scope: Literal["all", "rss", "download"] = "all"


@router.post("/run-once")
async def run_once(
    request: Request,
    body: SchedulerRunIn | None = None,
    *,
    settings: SettingsDep,
    storage: StorageDep,
    bus: BusDep,
) -> dict[str, object]:
    """手动触发与调度器同一批入口；进程内同一时间只允许一轮。"""
    body = body or SchedulerRunIn()
    if getattr(request.app.state, "scheduler_run_running", False):
        raise HTTPException(status_code=409, detail="a scheduler run is already active")
    request.app.state.scheduler_run_running = True
    components = getattr(request.app.state, "loop_components", None)
    owns_components = components is None
    if components is None:
        components = build_loop(settings, storage=storage, bus=bus)
    reports: dict[str, object] = {}
    errors: list[str] = []
    started = Event(
        EventCategory.SYSTEM,
        "scheduler.run.started",
        {"scope": body.scope},
    )
    await bus.publish(started)
    try:
        now = datetime.now(UTC)
        if body.scope in {"all", "rss"}:
            rss = await components.rss_poller.poll_all(now=now)
            reports["rss"] = {
                "picked": rss.picked,
                "gaps": rss.all_gaps,
                "errors": list(rss.errors),
            }
            errors.extend(rss.errors)
        if body.scope in {"all", "download"}:
            download = await components.download_poller.poll_once(now=now)
            reports["download"] = {
                "checked": download.checked,
                "completed": download.completed,
                "failed": download.failed,
                "retried": download.retried,
            }
        result = {"scope": body.scope, "reports": reports, "errors": errors}
        await bus.publish(
            Event(
                EventCategory.SYSTEM,
                "scheduler.run.completed",
                result,
            )
        )
        return result
    except Exception as exc:
        errors.append(type(exc).__name__)
        await bus.publish(
            Event(
                EventCategory.SYSTEM,
                "scheduler.run.failed",
                {"scope": body.scope, "error": type(exc).__name__},
            )
        )
        raise HTTPException(
            status_code=500, detail=f"scheduler run failed: {type(exc).__name__}"
        ) from exc
    finally:
        request.app.state.scheduler_run_running = False
        if owns_components:
            await components.close()


__all__ = ["router"]
