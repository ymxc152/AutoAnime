"""SSE 流单测（E2）：回放/心跳/Last-Event-ID 解析，一律离线（内存库 + 假总线）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from autoanime.core.events import Event, EventCategory, InMemoryEventBus
from autoanime.core.models import AuditLog
from autoanime.memory.store import SqliteStorage
from autoanime.web.queries import ApiStore
from autoanime.web.sse import SseOptions, event_stream, parse_last_event_id
from autoanime.core.enums import Actor


@pytest.fixture
async def storage() -> SqliteStorage:
    store = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await store.create_all()
    yield store
    await store.close()


async def _seed_audit(store: SqliteStorage, rows: list[AuditLog]) -> list[int]:
    ids = []
    for row in rows:
        await store.add(row)
        ids.append(row.id)
    return ids


def _audit_row(operation_id: str = "op1") -> AuditLog:
    return AuditLog(
        operation_id=operation_id,
        entity="parse_memory",
        entity_id=1,
        action="memory_hit",
        instruction={"k": "v"},
        reverse={},
        actor=Actor.AUTO,
    )


def test_parse_last_event_id_variants() -> None:
    assert parse_last_event_id("12", None) == 12
    assert parse_last_event_id(None, "34") == 34
    assert parse_last_event_id("12", "34") == 12  # header 优先
    assert parse_last_event_id(None, None) is None
    assert parse_last_event_id("not-an-int", None) is None  # 非整数按缺失
    assert parse_last_event_id("  ", "  ") is None


async def test_event_stream_replays_after_last_event_id(storage: SqliteStorage) -> None:
    ids = await _seed_audit(
        storage,
        [_audit_row("op1"), _audit_row("op2"), _audit_row("op3")],
    )
    api_store = ApiStore(storage)
    bus = InMemoryEventBus()
    stream = event_stream(
        store=api_store,
        bus=bus,
        options=SseOptions(heartbeat_s=30.0, replay_limit=50),
        last_event_id=ids[0],
    )
    first = await asyncio.wait_for(stream.__anext__(), timeout=2)
    assert first.retry == 3000
    replayed = [await asyncio.wait_for(stream.__anext__(), timeout=2) for _ in range(2)]
    encoded = [frame.encode().decode("utf-8") for frame in replayed]
    assert f"id: {ids[1]}" in encoded[0]
    assert "event: parse" in encoded[0]
    payload = json.loads(replayed[0].data)
    assert payload["category"] == "parse"
    assert payload["payload"]["operation_id"] == "op2"
    # 之后是静默等待（30s 心跳内不会再有帧）：验证无多余回放后收尾。
    await stream.aclose()


async def test_event_stream_explicit_replay_recent(storage: SqliteStorage) -> None:
    ids = await _seed_audit(storage, [_audit_row("op1"), _audit_row("op2")])
    api_store = ApiStore(storage)
    stream = event_stream(
        store=ApiStore(storage),
        bus=InMemoryEventBus(),
        options=SseOptions(heartbeat_s=30.0, replay_limit=50),
        replay=1,
    )
    await asyncio.wait_for(stream.__anext__(), timeout=2)  # retry 帧
    frame = await asyncio.wait_for(stream.__anext__(), timeout=2)
    assert f"id: {ids[-1]}" in frame.encode().decode("utf-8")  # 最近 1 条 = 最大 id
    await stream.aclose()


async def test_event_stream_replay_respects_limit(storage: SqliteStorage) -> None:
    await _seed_audit(storage, [_audit_row("op1"), _audit_row("op2")])
    stream = event_stream(
        store=ApiStore(storage),
        bus=InMemoryEventBus(),
        options=SseOptions(heartbeat_s=30.0, replay_limit=1),
        replay=5,
    )
    await asyncio.wait_for(stream.__anext__(), timeout=2)  # retry 帧
    frame = await asyncio.wait_for(stream.__anext__(), timeout=2)
    assert frame.encode().decode("utf-8").count("id: ") == 1
    # 回放封顶后不再有第二帧可立即取到（下一条是 30s 心跳）。
    await stream.aclose()


async def test_event_stream_delivers_live_events_with_heartbeat(storage: SqliteStorage) -> None:
    api_store = ApiStore(storage)
    bus = InMemoryEventBus()
    stream = event_stream(
        store=api_store,
        bus=bus,
        options=SseOptions(heartbeat_s=0.01, replay_limit=50),
    )
    await asyncio.wait_for(stream.__anext__(), timeout=2)  # retry 帧

    await bus.publish(
        Event(
            category=EventCategory.ORGANIZE,
            message="organize.rolled_back",
            payload={"audit_id": 99},
        )
    )
    live = await asyncio.wait_for(stream.__anext__(), timeout=2)
    encoded = live.encode().decode("utf-8")
    assert "event: organize" in encoded
    assert "id: 99" in encoded

    heartbeat = await asyncio.wait_for(stream.__anext__(), timeout=2)
    assert heartbeat.comment == "heartbeat"
    await stream.aclose()
    # 生成器 finally 已退订：总线上不再有订阅者。
    assert bus.subscriber_count() == 0


async def test_event_stream_audit_to_event_category_mapping(storage: SqliteStorage) -> None:
    from autoanime.web.learning import audit_to_event

    organize_row = AuditLog(
        operation_id="op", entity="episode", entity_id=2, action="archived",
        instruction={}, reverse={}, actor=Actor.AUTO,
    )
    unknown_row = AuditLog(
        operation_id="op", entity="weird_entity", entity_id=None, action="x",
        instruction={}, reverse={}, actor=Actor.AUTO,
    )
    assert audit_to_event(organize_row).category is EventCategory.ORGANIZE
    assert audit_to_event(unknown_row).category is EventCategory.SYSTEM
