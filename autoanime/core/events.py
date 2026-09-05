from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class EventCategory(StrEnum):
    PARSE = "parse"
    DOWNLOAD = "download"
    ORGANIZE = "organize"
    ERROR = "error"
    NOTIFY = "notify"
    SYSTEM = "system"


@dataclass(frozen=True)
class Event:
    category: EventCategory
    message: str
    payload: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class EventBus(Protocol):
    """Bus placeholder; PR1 intentionally does not provide a concrete implementation."""

    async def publish(self, event: Event) -> None: ...


# ---------------------------------------------------------------------------
# 进程内事件总线（E2 M3 后端增量；不改上方既有事件类/协议）
#
# D16 拍板：v1 单 backend 进程，内存事件总线仅进程内有效；SSE 重放基于
# 落库数据（audit_log），本总线只负责「在线扇出」。实例由应用 lifespan
# 持有（app.state），不引入模块级可变全局状态。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventSubscription:
    """One subscriber handle: an asyncio queue plus the bus-side unsubscribe hook."""

    queue: asyncio.Queue[Event | None]
    _bus: InMemoryEventBus

    def close(self) -> None:
        """Unsubscribe; idempotent. A ``None`` sentinel wakes the consumer."""
        if self.queue not in self._bus._queues:
            return  # 已关闭：不重复投递哨兵。
        self._bus.unsubscribe(self.queue)
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            # 队列满则挤掉最旧一帧，保证哨兵一定可达（消费端据此退出）。
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(None)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


class InMemoryEventBus:
    """Concrete in-process :class:`EventBus` with per-subscriber bounded queues.

    - ``publish`` 扇出到所有订阅者，绝不阻塞：队列满时丢弃该订阅者最旧的
      事件（慢消费者只影响自己，不拖垮发布方——SSE 场景即「漏报由落库
      回放兜底」的语义）。
    - ``unsubscribe``/``close`` 后向队列投递 ``None`` 哨兵，消费端据此退出。
    """

    def __init__(self, max_queue_size: int = 512) -> None:
        self._max_queue_size = max_queue_size
        self._queues: set[asyncio.Queue[Event | None]] = set()

    def subscribe(self) -> EventSubscription:
        queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=self._max_queue_size)
        self._queues.add(queue)
        return EventSubscription(queue=queue, _bus=self)

    def unsubscribe(self, queue: asyncio.Queue[Event | None]) -> None:
        self._queues.discard(queue)

    async def publish(self, event: Event) -> None:
        for queue in tuple(self._queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest entry for this slow consumer, then enqueue.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def subscriber_count(self) -> int:
        return len(self._queues)
