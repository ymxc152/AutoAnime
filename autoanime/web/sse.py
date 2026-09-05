"""SSE 流（GET /api/events）：桥接进程内事件总线（D16）+ 落库回放。

- 在线通道：``InMemoryEventBus`` 扇出，事件分类（EventCategory）原样透传；
- 回放通道：``Last-Event-ID``（或 ``last_event_id`` query）→ 按 audit_log.id
  升序补发其后事件（防漏报，D16「重放基于落库数据」）；``replay=N`` 显式
  重放最近 N 条；
- 心跳：无消息超过 ``api_sse_heartbeat_s`` 发 ``: heartbeat`` 注释帧防代理
  超时；客户端断线由 ASGI 取消驱动，``finally`` 统一退订清理。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from sse_starlette.event import ServerSentEvent

from autoanime.core.events import Event, InMemoryEventBus
from autoanime.web.learning import audit_to_event
from autoanime.web.queries import ApiStore

logger = logging.getLogger(__name__)

_RETRY_MS = 3000


@dataclass(frozen=True)
class SseOptions:
    heartbeat_s: float
    replay_limit: int


def parse_last_event_id(last_event_id_header: str | None, query_value: str | None) -> int | None:
    """Last-Event-ID 解析：header 优先，query 兜底；非整数按缺失处理。"""
    for candidate in (last_event_id_header, query_value):
        if candidate is None:
            continue
        text = candidate.strip()
        if not text:
            continue
        try:
            return int(text)
        except ValueError:
            continue
    return None


def _sse_from_event(event: Event) -> ServerSentEvent:
    audit_id = event.payload.get("audit_id")
    event_id = str(audit_id) if isinstance(audit_id, int) else None
    return ServerSentEvent(
        data=json.dumps(
            {"category": event.category.value, "message": event.message, "payload": event.payload},
            ensure_ascii=False,
        ),
        event=event.category.value,
        id=event_id,
    )


async def event_stream(
    *,
    store: ApiStore,
    bus: InMemoryEventBus,
    options: SseOptions,
    last_event_id: int | None = None,
    replay: int | None = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    """SSE 主体生成器；退出路径（断线/服务关闭）统一在 finally 退订。"""
    subscription = bus.subscribe()
    try:
        yield ServerSentEvent(retry=_RETRY_MS)
        try:
            if last_event_id is not None:
                replayed = await store.replay_after(
                    last_event_id, limit=max(0, options.replay_limit)
                )
            elif replay is not None and replay > 0:
                replayed = await store.replay_recent(
                    limit=min(replay, max(0, options.replay_limit))
                )
            else:
                replayed = []
        except Exception:
            # 回放失败不阻断在线通道（防漏报尽力而为）。
            logger.warning("sse replay failed; continuing live-only", exc_info=True)
            replayed = []
        for row in replayed:
            yield _sse_from_event(audit_to_event(row))

        while True:
            try:
                event = await asyncio.wait_for(
                    subscription.queue.get(), timeout=options.heartbeat_s
                )
            except TimeoutError:
                yield ServerSentEvent(comment="heartbeat")
                continue
            if event is None:
                break
            yield _sse_from_event(event)
    finally:
        subscription.close()
