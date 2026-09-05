"""PR6 P2：reference_cache 表 + CachedReference 缓存包装器 + token bucket 频控。

全部离线：adapter 用计数 fake，DB 用内存 SQLite，clock/sleeper/now 注入。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config

from autoanime.core.interfaces import MetadataReference, Registry
from autoanime.core.models import ReferenceCache
from autoanime.memory.reference_cache import (
    DEFAULT_NEGATIVE_TTL_S,
    DEFAULT_POSITIVE_TTL_S,
    CachedReference,
    TokenBucketLimiter,
    compute_bucket_wait,
    facts_from_json,
    facts_to_json,
    is_negative_json,
    refill_tokens,
)
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l3.reference import ReferenceChain, ReferenceFacts
from autoanime.providers import register_reference_providers

FACTS = ReferenceFacts(
    canonical_title="葬送のフリーレン",
    seasons=(1,),
    episode_count=28,
    aliases=("Frieren", "葬送的芙莉莲"),
    source="bangumi",
)

TITLE_SHAPE = "somosomo no furiren s{season}e{ep}"


class FakeAdapter:
    """计数 fake adapter：返回预置 facts 或 None。"""

    def __init__(self, facts: ReferenceFacts | None) -> None:
        self.facts = facts
        self.count = 0
        self.shapes: list[str] = []
        self.closed = False

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        self.count += 1
        self.shapes.append(title_shape)
        return self.facts

    async def aclose(self) -> None:
        self.closed = True


class FakeNow:
    """可推进的注入时钟（TTL 判定）。"""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 6, 12, 0, 0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeClock:
    """单调秒表时钟（频控）。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeSleeper:
    """记录睡眠时长并推进 FakeClock。"""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.sleeps: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.clock.now += seconds


class CountingLimiter(TokenBucketLimiter):
    """只计数不限流的 limiter（qps=0 直通）。"""

    def __init__(self) -> None:
        super().__init__(qps=0.0)
        self.acquires = 0

    async def acquire(self) -> None:
        self.acquires += 1
        await super().acquire()


class StubCacheStore:
    """内存版 ReferenceCacheStore（无 DB）。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ReferenceCache] = {}

    async def find_reference_cache(
        self, title_shape: str, provider: str
    ) -> ReferenceCache | None:
        return self.rows.get((title_shape, provider))

    async def add_reference_cache(self, row: ReferenceCache) -> None:
        self.rows[(row.title_shape, row.provider)] = row


async def make_store() -> SqliteStorage:
    store = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await store.create_all()
    return store


def make_cached(
    adapter: FakeAdapter,
    store: Any,
    *,
    now: FakeNow | None = None,
    limiter: TokenBucketLimiter | None = None,
    positive_ttl_s: float = DEFAULT_POSITIVE_TTL_S,
    negative_ttl_s: float = DEFAULT_NEGATIVE_TTL_S,
    provider: str = "bangumi",
) -> CachedReference:
    return CachedReference(
        provider=provider,
        upstream=adapter,
        store=store,
        now_fn=now if now is not None else datetime.now,
        positive_ttl_s=positive_ttl_s,
        negative_ttl_s=negative_ttl_s,
        limiter=limiter,
    )


# ---------------------------------------------------------------------------
# facts JSON 序列化
# ---------------------------------------------------------------------------


def test_facts_json_round_trip() -> None:
    assert facts_from_json(facts_to_json(FACTS)) == FACTS


def test_facts_json_defensive_parse() -> None:
    # 字段逐一防御式解析：类型不符的字段落回默认值，合法字段保留。
    facts = facts_from_json(
        {
            "canonical_title": 1,
            "seasons": ["x", 2],
            "episode_count": True,
            "aliases": "not-a-list",
            "source": None,
        }
    )
    assert facts == ReferenceFacts(seasons=(2,))


def test_is_negative_json() -> None:
    assert is_negative_json({"negative": True})
    assert not is_negative_json({"canonical_title": "x"})
    assert not is_negative_json("not-a-dict")
    assert facts_from_json({"negative": True}) is None


# ---------------------------------------------------------------------------
# store 增量方法
# ---------------------------------------------------------------------------


async def test_find_and_add_reference_cache_round_trip() -> None:
    store = await make_store()
    try:
        assert await store.find_reference_cache(TITLE_SHAPE, "bangumi") is None
        await store.add_reference_cache(
            ReferenceCache(
                title_shape=TITLE_SHAPE,
                provider="bangumi",
                facts=facts_to_json(FACTS),
                fetched_at=datetime(2026, 9, 6),
                expires_at=datetime(2026, 10, 6),
            )
        )
        row = await store.find_reference_cache(TITLE_SHAPE, "bangumi")
        assert row is not None
        assert facts_from_json(row.facts) == FACTS

        # 同键覆盖（upsert），不会因 unique 约束报错。
        await store.add_reference_cache(
            ReferenceCache(
                title_shape=TITLE_SHAPE,
                provider="bangumi",
                facts={"negative": True},
                fetched_at=datetime(2026, 9, 7),
                expires_at=datetime(2026, 9, 8),
            )
        )
        row = await store.find_reference_cache(TITLE_SHAPE, "bangumi")
        assert row is not None
        assert is_negative_json(row.facts)
        assert row.fetched_at == datetime(2026, 9, 7)
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# CachedReference：正/负缓存与 TTL
# ---------------------------------------------------------------------------


async def test_positive_cache_hit_skips_adapter() -> None:
    store = await make_store()
    try:
        adapter = FakeAdapter(FACTS)
        cached = make_cached(adapter, store)
        assert await cached.lookup(TITLE_SHAPE) == FACTS
        assert await cached.lookup(TITLE_SHAPE) == FACTS
        assert adapter.count == 1
    finally:
        await store.close()


async def test_negative_cache_prevents_repeat_adapter_calls() -> None:
    store = await make_store()
    try:
        adapter = FakeAdapter(None)
        cached = make_cached(adapter, store)
        assert await cached.lookup(TITLE_SHAPE) is None
        assert await cached.lookup(TITLE_SHAPE) is None
        assert adapter.count == 1
        row = await store.find_reference_cache(TITLE_SHAPE, "bangumi")
        assert row is not None
        assert is_negative_json(row.facts)
    finally:
        await store.close()


async def test_positive_ttl_expiry_requeries_adapter() -> None:
    store = await make_store()
    try:
        now = FakeNow()
        adapter = FakeAdapter(FACTS)
        cached = make_cached(adapter, store, now=now, positive_ttl_s=100.0)
        await cached.lookup(TITLE_SHAPE)
        now.advance(50.0)
        assert await cached.lookup(TITLE_SHAPE) == FACTS
        assert adapter.count == 1  # 未过期，仍走缓存
        now.advance(51.0)
        await cached.lookup(TITLE_SHAPE)
        assert adapter.count == 2  # 过期后重新打 adapter
    finally:
        await store.close()


async def test_negative_ttl_shorter_than_positive() -> None:
    store = await make_store()
    try:
        now = FakeNow()
        adapter = FakeAdapter(None)
        cached = make_cached(adapter, store, now=now)
        await cached.lookup(TITLE_SHAPE)
        row = await store.find_reference_cache(TITLE_SHAPE, "bangumi")
        assert row is not None
        assert row.fetched_at is not None and row.expires_at is not None
        assert row.expires_at - row.fetched_at == timedelta(seconds=DEFAULT_NEGATIVE_TTL_S)
        now.advance(DEFAULT_NEGATIVE_TTL_S + 1.0)
        await cached.lookup(TITLE_SHAPE)
        assert adapter.count == 2  # 负缓存过期后重新打 adapter
    finally:
        await store.close()


async def test_default_positive_ttl_is_30_days() -> None:
    store = await make_store()
    try:
        now = FakeNow()
        adapter = FakeAdapter(FACTS)
        cached = make_cached(adapter, store, now=now)
        await cached.lookup(TITLE_SHAPE)
        row = await store.find_reference_cache(TITLE_SHAPE, "bangumi")
        assert row is not None
        assert row.fetched_at is not None and row.expires_at is not None
        assert row.expires_at - row.fetched_at == timedelta(days=30)
    finally:
        await store.close()


async def test_cache_keys_are_per_provider() -> None:
    store = await make_store()
    try:
        bangumi_adapter = FakeAdapter(FACTS)
        tmdb_adapter = FakeAdapter(
            ReferenceFacts(canonical_title="Frieren", source="tmdb")
        )
        cached_bangumi = make_cached(bangumi_adapter, store, provider="bangumi")
        cached_tmdb = make_cached(tmdb_adapter, store, provider="tmdb")
        await cached_bangumi.lookup(TITLE_SHAPE)
        await cached_tmdb.lookup(TITLE_SHAPE)
        # 同一 title_shape 在两个 provider 下互不命中。
        assert bangumi_adapter.count == 1
        assert tmdb_adapter.count == 1
        await cached_bangumi.lookup(TITLE_SHAPE)
        assert bangumi_adapter.count == 1
    finally:
        await store.close()


async def test_cache_store_failure_degrades_to_upstream() -> None:
    class BrokenStore:
        async def find_reference_cache(
            self, title_shape: str, provider: str
        ) -> ReferenceCache | None:
            raise RuntimeError("db down")

        async def add_reference_cache(self, row: ReferenceCache) -> None:
            raise RuntimeError("db down")

    adapter = FakeAdapter(FACTS)
    cached = make_cached(adapter, BrokenStore())
    assert await cached.lookup(TITLE_SHAPE) == FACTS
    assert await cached.lookup(TITLE_SHAPE) == FACTS
    assert adapter.count == 2  # 缓存不可用时每查必打 adapter，但不抛异常


async def test_limiter_applies_only_on_cache_miss() -> None:
    store = await make_store()
    try:
        adapter = FakeAdapter(FACTS)
        limiter = CountingLimiter()
        cached = make_cached(adapter, store, limiter=limiter)
        await cached.lookup(TITLE_SHAPE)
        await cached.lookup(TITLE_SHAPE)  # 缓存命中，不过频控
        assert limiter.acquires == 1
        assert adapter.count == 1
    finally:
        await store.close()


async def test_aclose_delegates_to_upstream() -> None:
    adapter = FakeAdapter(FACTS)
    cached = make_cached(adapter, StubCacheStore())
    await cached.aclose()
    assert adapter.closed


async def test_cached_reference_satisfies_metadata_reference_protocol() -> None:
    cached = make_cached(FakeAdapter(FACTS), StubCacheStore())
    assert isinstance(cached, MetadataReference)


# ---------------------------------------------------------------------------
# token bucket：纯函数 + limiter 实例状态
# ---------------------------------------------------------------------------


def test_refill_tokens_pure_function() -> None:
    assert refill_tokens(0.5, None, 10.0, rate=2.0, capacity=4.0) == 4.0
    assert refill_tokens(0.5, 10.0, 11.0, rate=2.0, capacity=4.0) == 2.5
    assert refill_tokens(0.5, 10.0, 12.0, rate=2.0, capacity=4.0) == 4.0
    assert refill_tokens(0.5, 12.0, 10.0, rate=2.0, capacity=4.0) == 0.5  # 时钟回拨不凭空补充


def test_compute_bucket_wait_pure_function() -> None:
    assert compute_bucket_wait(1.0, rate=2.0) == 0.0
    assert compute_bucket_wait(0.0, rate=2.0) == 0.5
    assert compute_bucket_wait(0.5, rate=2.0) == 0.25
    assert compute_bucket_wait(0.0, rate=0.0) == 0.0  # rate<=0 不限流


async def test_token_bucket_limiter_sleeps_at_rate() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    limiter = TokenBucketLimiter(qps=2.0, capacity=1.0, clock=clock, sleeper=sleeper)
    await limiter.acquire()  # 首次免等待
    assert sleeper.sleeps == []
    await limiter.acquire()
    await limiter.acquire()
    assert sleeper.sleeps == [0.5, 0.5]


async def test_token_bucket_allows_burst_up_to_capacity() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    limiter = TokenBucketLimiter(qps=2.0, capacity=2.0, clock=clock, sleeper=sleeper)
    await limiter.acquire()
    await limiter.acquire()
    assert sleeper.sleeps == []
    await limiter.acquire()
    assert sleeper.sleeps == [0.5]


async def test_token_bucket_qps_zero_is_unlimited() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    limiter = TokenBucketLimiter(qps=0.0, clock=clock, sleeper=sleeper)
    assert limiter.limited is False
    for _ in range(5):
        await limiter.acquire()
    assert sleeper.sleeps == []


# ---------------------------------------------------------------------------
# Registry / ReferenceChain 接线（chain 契约零改动）
# ---------------------------------------------------------------------------


async def test_reference_chain_uses_cached_wrapper_unchanged() -> None:
    store = await make_store()
    try:
        adapter = FakeAdapter(FACTS)
        registry = Registry()
        registry.register(MetadataReference, "bangumi")(
            make_cached(adapter, store, provider="bangumi")
        )
        chain = ReferenceChain(registry, order=("bangumi",))
        assert chain.names == ("bangumi",)
        assert await chain.lookup(TITLE_SHAPE) == FACTS
        assert await chain.lookup(TITLE_SHAPE) == FACTS
        assert adapter.count == 1  # 二次查询走缓存，不触 adapter
    finally:
        await store.close()


def test_register_reference_providers_wraps_when_cache_store_given() -> None:
    registry = Registry()
    register_reference_providers(
        registry, cache_store=StubCacheStore(), reference_qps=2.0
    )
    for name in ("bangumi", "tmdb"):
        wrapped = registry.get(MetadataReference, name)
        assert isinstance(wrapped, CachedReference)
        assert isinstance(wrapped, MetadataReference)
        assert isinstance(wrapped._limiter, TokenBucketLimiter)


def test_register_reference_providers_without_cache_keeps_bare_adapters() -> None:
    from autoanime.providers import BangumiReference, TmdbReference

    registry = Registry()
    register_reference_providers(registry)
    assert isinstance(registry.get(MetadataReference, "bangumi"), BangumiReference)
    assert isinstance(registry.get(MetadataReference, "tmdb"), TmdbReference)


# ---------------------------------------------------------------------------
# alembic 0003：upgrade/downgrade
# ---------------------------------------------------------------------------


def test_migration_0003_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "migration.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(config, "0003")
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # UNIQUE(title_shape, provider) 在 SQLite 由自动唯一索引实现：
        # 验证存在覆盖两列的唯一索引。
        unique_columns: set[tuple[str, ...]] = set()
        for index_name, is_unique in connection.execute(
            "SELECT name, \"unique\" FROM pragma_index_list('reference_cache')"
        ):
            if not is_unique:
                continue
            info = connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
            unique_columns.add(tuple(str(row[2]) for row in info))
    assert "reference_cache" in tables
    assert ("title_shape", "provider") in unique_columns

    command.downgrade(config, "0002")
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "reference_cache" not in tables
