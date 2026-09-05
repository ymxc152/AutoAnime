"""InMemoryEventBus（E2 事件总线增量）单元测试：一律离线。"""

from __future__ import annotations

import asyncio

from autoanime.core.events import Event, EventCategory, InMemoryEventBus


def _event(message: str = "hello") -> Event:
    return Event(category=EventCategory.SYSTEM, message=message, payload={"k": 1})


async def test_publish_fans_out_to_all_subscribers() -> None:
    bus = InMemoryEventBus()
    sub_a = bus.subscribe()
    sub_b = bus.subscribe()

    await bus.publish(_event())

    assert (await asyncio.wait_for(sub_a.queue.get(), timeout=1)).message == "hello"
    assert (await asyncio.wait_for(sub_b.queue.get(), timeout=1)).message == "hello"


async def test_unsubscribe_stops_delivery_and_close_is_idempotent() -> None:
    bus = InMemoryEventBus()
    sub = bus.subscribe()
    sub.close()
    assert bus.subscriber_count() == 0
    # 首次 close 后队列里恰好一个 None 哨兵（唤醒消费端退出）。
    assert await asyncio.wait_for(sub.queue.get(), timeout=1) is None

    sub.close()  # 幂等：不再重复投递哨兵
    await bus.publish(_event())  # 已退订：不再接收新事件

    assert sub.queue.empty()


async def test_publish_without_subscribers_is_a_noop() -> None:
    bus = InMemoryEventBus()
    await bus.publish(_event())
    assert bus.subscriber_count() == 0


async def test_slow_consumer_drops_oldest_not_blocks_publisher() -> None:
    bus = InMemoryEventBus(max_queue_size=2)
    sub = bus.subscribe()
    for index in range(5):
        await bus.publish(_event(f"e{index}"))

    messages = [sub.queue.get_nowait().message for _ in range(2)]
    # 队列保持有界：最旧的 e0/e1/e2 被丢弃，保留最新的 e3/e4。
    assert messages == ["e3", "e4"]
    assert sub.queue.qsize() == 0


async def test_close_sentinel_wakes_consumer() -> None:
    bus = InMemoryEventBus()
    sub = bus.subscribe()

    async def _close_soon() -> None:
        await asyncio.sleep(0.01)
        sub.close()

    closer = asyncio.create_task(_close_soon())
    first = await asyncio.wait_for(sub.queue.get(), timeout=1)
    await closer
    assert first is None


def test_bus_satisfies_event_bus_protocol() -> None:
    from autoanime.core.events import EventBus

    assert isinstance(InMemoryEventBus(), EventBus)
