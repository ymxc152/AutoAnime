"""SSE 端点（GET /api/events）。

B7：EventSource 无法自定义 header，token 亦可经 ``?token=`` 传递
（认证中间件统一处理；默认空 token 时无感）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from sse_starlette.sse import EventSourceResponse

from autoanime.web.deps import ApiStoreDep, BusDep, SettingsDep
from autoanime.web.sse import SseOptions, event_stream, parse_last_event_id

router = APIRouter(tags=["events"])


@router.get("/events")
async def stream_events(
    request: Request,
    store: ApiStoreDep,
    bus: BusDep,
    settings: SettingsDep,
    replay: Annotated[
        int | None,
        Query(ge=0, description="无 Last-Event-ID 时显式重放最近 N 条"),
    ] = None,
    last_event_id: Annotated[str | None, Query(description="Last-Event-ID 的 query 兜底")] = None,
) -> EventSourceResponse:
    parsed = parse_last_event_id(
        request.headers.get("last-event-id"), last_event_id
    )
    options = SseOptions(
        heartbeat_s=settings.api_sse_heartbeat_s,
        replay_limit=settings.api_sse_replay_limit,
    )
    return EventSourceResponse(
        event_stream(
            store=store,
            bus=bus,
            options=options,
            last_event_id=parsed,
            replay=replay,
        ),
        ping=None,
    )
