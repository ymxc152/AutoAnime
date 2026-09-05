"""llm_cache 的 store 层读写（PR5 T2）。

覆盖 ``SqliteStorage.get_llm_cache`` / ``put_llm_cache`` 与
``StorageLlmCacheStore``（T1 ``LlmCacheStore`` Protocol 适配器）的语义：
按键精确读写、同键覆盖、未命中 ``None``、pattern_hash 唯一约束；
缓存响应解析失败按 miss 处理由调用方负责，store 层无脑存取。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from autoanime.core.interfaces import LlmCacheStore
from autoanime.core.models import LlmCacheRow
from autoanime.memory.store import SqliteStorage, StorageLlmCacheStore
from autoanime.pipeline.l3.cache_key import LlmCache


async def _make_store() -> SqliteStorage:
    store = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await store.create_all()
    return store


async def test_get_misses_before_write() -> None:
    store = await _make_store()
    try:
        assert await store.get_llm_cache("p1") is None
    finally:
        await store.close()


async def test_put_then_get_round_trips_to_l3_dataclass() -> None:
    store = await _make_store()
    try:
        recorded = LlmCache(pattern_hash="p1", response='{"title": "X"}', model="m1")
        await store.put_llm_cache(recorded)
        assert await store.get_llm_cache("p1") == recorded
    finally:
        await store.close()


async def test_put_without_model_keeps_model_none() -> None:
    store = await _make_store()
    try:
        await store.put_llm_cache(LlmCache(pattern_hash="p1", response="r"))
        got = await store.get_llm_cache("p1")
        assert got == LlmCache(pattern_hash="p1", response="r")
        assert got is not None and got.model is None
    finally:
        await store.close()


async def test_put_same_pattern_hash_overwrites() -> None:
    store = await _make_store()
    try:
        await store.put_llm_cache(LlmCache(pattern_hash="p1", response="old", model="m-old"))
        await store.put_llm_cache(LlmCache(pattern_hash="p1", response="new", model=None))
        got = await store.get_llm_cache("p1")
        assert got == LlmCache(pattern_hash="p1", response="new")
        assert got is not None and got.model is None
    finally:
        await store.close()


async def test_keys_are_independent() -> None:
    store = await _make_store()
    try:
        await store.put_llm_cache(LlmCache(pattern_hash="p1", response="r1"))
        await store.put_llm_cache(LlmCache(pattern_hash="p2", response="r2"))
        got1 = await store.get_llm_cache("p1")
        got2 = await store.get_llm_cache("p2")
        assert got1 is not None and got1.response == "r1"
        assert got2 is not None and got2.response == "r2"
        assert await store.get_llm_cache("p3") is None
    finally:
        await store.close()


async def test_pattern_hash_is_unique_at_schema_level() -> None:
    store = await _make_store()
    try:
        await store.put_llm_cache(LlmCache(pattern_hash="dup", response="a"))
        with pytest.raises(IntegrityError):
            await store.add(LlmCacheRow(pattern_hash="dup", response_text="b"))
    finally:
        await store.close()


async def test_row_timestamp_is_stamped() -> None:
    store = await _make_store()
    try:
        await store.put_llm_cache(LlmCache(pattern_hash="p1", response="r"))
        async with store.transaction() as session:
            row = (
                await session.execute(select(LlmCacheRow))
            ).scalar_one()
            assert row.created_at is not None
            assert row.request_fingerprint is None
    finally:
        await store.close()


async def test_adapter_satisfies_llm_cache_store_protocol() -> None:
    store = await _make_store()
    try:
        adapter: LlmCacheStore = StorageLlmCacheStore(store)
        assert isinstance(adapter, LlmCacheStore)
        await adapter.put(LlmCache(pattern_hash="k", response="r", model="m"))
        assert await adapter.get("k") == LlmCache(pattern_hash="k", response="r", model="m")
        assert await adapter.get("missing") is None
    finally:
        await store.close()
