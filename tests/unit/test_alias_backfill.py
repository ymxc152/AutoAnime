"""PR7 M3：confirm 侧 title_aliases 回填（写侧基础设施）单元测试。

覆盖：fake 参考源回填成功（含 CachedReference 包装、缓存命中零外呼）、
参考源失败/超时/miss 静默跳过且 confirm 主流程不受影响、self 映射不写、
store 层 ``find_alias_key``/``put_alias_map`` 行为、alembic 0004
upgrade/downgrade。全部离线。
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseResult
from autoanime.core.models import ParseMemory, TitleAlias
from autoanime.memory import learn as learn_module
from autoanime.memory.learn import (
    ALIAS_BACKFILL_TIMEOUT_S,
    StorageMemoryAccess,
    backfill_title_aliases,
    learn_confirmation,
)
from autoanime.memory.reference_cache import CachedReference
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l3.reference import ReferenceFacts

_MEMORY_DB = "sqlite+aiosqlite:///:memory:"

CANONICAL_TITLE = "葬送のフリーレン"
CANONICAL_SHAPE = CANONICAL_TITLE  # 无结构标记，shape 即 casefold 原样
QUERY_TITLE = "葬送的芙莉莲"
QUERY_SHAPE = QUERY_TITLE

FACTS = ReferenceFacts(
    canonical_title=CANONICAL_TITLE,
    seasons=(1,),
    episode_count=28,
    aliases=("Frieren", QUERY_TITLE),
    source="bangumi",
)


class FakeReference:
    """计数 fake 参考源：返回预置 facts，或按脚本抛错/延迟。"""

    def __init__(self, facts: ReferenceFacts | None = FACTS) -> None:
        self.facts = facts
        self.count = 0
        self.shapes: list[str] = []
        self.error: Exception | None = None
        self.delay_s: float = 0.0

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        self.count += 1
        self.shapes.append(title_shape)
        if self.error is not None:
            raise self.error
        if self.delay_s > 0.0:
            await asyncio.sleep(self.delay_s)
        return self.facts


def _confirmed(title: str = QUERY_TITLE) -> ParseResult:
    return ParseResult(
        title=title,
        season=1,
        episode=1,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.HIGH,
        confidence=1.0,
        missing_fields=(),
        evidence={},
    )


@pytest.fixture
async def storage():
    store = SqliteStorage(_MEMORY_DB)
    async with store:
        yield store


async def _alias_rows(storage: SqliteStorage) -> dict[str, TitleAlias]:
    rows = await storage.list(TitleAlias)
    return {row.title_shape_norm: row for row in rows}


# ---------------------------------------------------------------------------
# 回填成功：alias 映射写入 + confirm 主流程照常
# ---------------------------------------------------------------------------


async def test_backfill_writes_alias_mapping(storage: SqliteStorage) -> None:
    reference = FakeReference()
    access = StorageMemoryAccess(storage)
    outcome = await learn_confirmation(
        access,
        confirmed=_confirmed(),
        raw_name="[Sub] Frieren - 01 [1080p].mkv",
        reference_lookup=reference,
    )

    # confirm 主流程不受影响：两级 ParseMemory 照常写入
    assert outcome.bypassed is False
    assert len(outcome.entries) == 2
    assert all(isinstance(entry, ParseMemory) for entry in outcome.entries)

    # 回填查了一次参考源，查询 shape 是 confirmed 标题的归一形状
    assert reference.count == 1
    assert reference.shapes == [QUERY_SHAPE]

    rows = await _alias_rows(storage)
    # canonical 自身之外的别名都有映射（"Frieren" → canonical）
    assert rows["frieren"].canonical_shape == CANONICAL_SHAPE
    # 查询侧确认时的标题形状也归一到 canonical
    assert rows[QUERY_SHAPE].canonical_shape == CANONICAL_SHAPE
    # source 记录 canonical 来源
    assert all(row.source == "bangumi" for row in rows.values())
    # canonical shape 自身的 self 映射不写
    assert CANONICAL_SHAPE not in rows
    assert len(rows) == 2


async def test_backfill_via_cached_reference_hits_cache_second_time(
    storage: SqliteStorage,
) -> None:
    """回填走 CachedReference：首次 miss 外呼并写缓存，二次 confirm 零外呼。"""

    class CountingAdapter:
        def __init__(self) -> None:
            self.count = 0

        async def lookup(self, title_shape: str) -> ReferenceFacts | None:
            self.count += 1
            return FACTS

    adapter = CountingAdapter()
    cached = CachedReference(provider="bangumi", upstream=adapter, store=storage)
    access = StorageMemoryAccess(storage)

    await learn_confirmation(
        access,
        confirmed=_confirmed(),
        raw_name="[Sub] Frieren - 01 [1080p].mkv",
        reference_lookup=cached,
    )
    await learn_confirmation(
        access,
        confirmed=_confirmed(),
        raw_name="[Sub] Frieren - 02 [1080p].mkv",
        reference_lookup=cached,
    )
    assert adapter.count == 1  # 第二次 confirm 命中 reference_cache，不触 adapter
    rows = await _alias_rows(storage)
    assert rows["frieren"].canonical_shape == CANONICAL_SHAPE


async def test_backfill_without_reference_lookup_writes_nothing(
    storage: SqliteStorage,
) -> None:
    """默认不传 reference_lookup：行为与既有 confirm 完全一致（语义不变）。"""
    access = StorageMemoryAccess(storage)
    outcome = await learn_confirmation(
        access,
        confirmed=_confirmed(),
        raw_name="[Sub] Frieren - 01 [1080p].mkv",
    )
    assert outcome.bypassed is False
    assert len(outcome.entries) == 2
    assert await _alias_rows(storage) == {}


async def test_bypassed_confirm_skips_backfill(storage: SqliteStorage) -> None:
    """bypass 命中时不写任何东西，也不触参考源。"""

    class FakeBypass:
        async def has_bypass(self, pattern_hash: str) -> bool:
            return True

    reference = FakeReference()
    access = StorageMemoryAccess(storage)
    outcome = await learn_confirmation(
        access,
        confirmed=_confirmed(),
        raw_name="anything",
        bypass_lookup=FakeBypass(),
        reference_lookup=reference,
    )
    assert outcome.bypassed is True
    assert reference.count == 0
    assert await _alias_rows(storage) == {}


# ---------------------------------------------------------------------------
# 护栏：参考源失败/超时/miss 一律静默，confirm 主流程仍成功
# ---------------------------------------------------------------------------


async def test_reference_failure_is_silent(storage: SqliteStorage) -> None:
    """参考源抛异常：回填静默跳过，confirm 主流程照常返回两级写入。"""
    reference = FakeReference()
    reference.error = RuntimeError("bangumi down")
    access = StorageMemoryAccess(storage)
    outcome = await learn_confirmation(
        access,
        confirmed=_confirmed(),
        raw_name="[Sub] Frieren - 01 [1080p].mkv",
        reference_lookup=reference,
    )
    assert outcome.bypassed is False
    assert len(outcome.entries) == 2
    assert await _alias_rows(storage) == {}


async def test_reference_timeout_is_silent(
    storage: SqliteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """参考源慢于严格超时：wait_for 掐断，静默跳过且不阻塞主流程。"""
    monkeypatch.setattr(learn_module, "ALIAS_BACKFILL_TIMEOUT_S", 0.05)
    reference = FakeReference()
    reference.delay_s = 5.0
    access = StorageMemoryAccess(storage)
    outcome = await learn_confirmation(
        access,
        confirmed=_confirmed(),
        raw_name="[Sub] Frieren - 01 [1080p].mkv",
        reference_lookup=reference,
    )
    assert outcome.bypassed is False
    assert len(outcome.entries) == 2
    assert await _alias_rows(storage) == {}


async def test_reference_miss_and_empty_canonical_are_silent(
    storage: SqliteStorage,
) -> None:
    """参考源 miss（None）与 canonical_title 为空都只跳过，不报错不写表。"""
    access = StorageMemoryAccess(storage)
    raw_name = "[Sub] Frieren - 01 [1080p].mkv"

    miss = FakeReference(facts=None)
    outcome = await learn_confirmation(
        access, confirmed=_confirmed(), raw_name=raw_name, reference_lookup=miss
    )
    assert len(outcome.entries) == 2
    assert await _alias_rows(storage) == {}

    no_canonical = FakeReference(
        ReferenceFacts(canonical_title=None, aliases=("Frieren",), source="bangumi")
    )
    outcome = await learn_confirmation(
        access, confirmed=_confirmed(), raw_name=raw_name, reference_lookup=no_canonical
    )
    assert len(outcome.entries) == 2
    assert await _alias_rows(storage) == {}


async def test_backfill_direct_call_never_raises(storage: SqliteStorage) -> None:
    """backfill_title_aliases 本体吞掉一切异常（含 store 写失败）。"""

    class BrokenStore:
        async def put_alias_map(self, mapping: dict[str, str], source: str) -> None:
            raise RuntimeError("disk full")

    reference = FakeReference()
    # 不抛错即通过
    await backfill_title_aliases(
        BrokenStore(),  # type: ignore[arg-type]
        reference,
        confirmed=_confirmed(),
    )
    assert reference.count == 1


async def test_self_mapping_not_written(storage: SqliteStorage) -> None:
    """confirmed 标题与全部别名归一后都等于 canonical：一个映射都不写。"""
    facts = ReferenceFacts(
        canonical_title="Frieren",
        aliases=("frieren", " FRIEREN "),
        source="bangumi",
    )
    reference = FakeReference(facts=facts)
    access = StorageMemoryAccess(storage)
    outcome = await learn_confirmation(
        access,
        confirmed=_confirmed("Frieren"),
        raw_name="[Sub] Frieren - 01 [1080p].mkv",
        reference_lookup=reference,
    )
    assert outcome.bypassed is False
    assert len(outcome.entries) == 2
    assert await _alias_rows(storage) == {}


# ---------------------------------------------------------------------------
# store 层：find_alias_key / put_alias_map
# ---------------------------------------------------------------------------


async def test_find_alias_key_miss_returns_none(storage: SqliteStorage) -> None:
    assert await storage.find_alias_key("never-learned") is None


async def test_put_alias_map_is_idempotent_and_overwrites(storage: SqliteStorage) -> None:
    mapping = {"frieren": CANONICAL_SHAPE, QUERY_SHAPE: CANONICAL_SHAPE}
    await storage.put_alias_map(mapping, "bangumi")
    await storage.put_alias_map(mapping, "bangumi")  # 重复写幂等

    rows = await _alias_rows(storage)
    assert set(rows) == {"frieren", QUERY_SHAPE}
    assert all(row.source == "bangumi" for row in rows.values())
    assert await storage.find_alias_key("frieren") == CANONICAL_SHAPE

    # 同一 alias shape 再次写入覆盖旧 canonical（每形状至多一行）
    await storage.put_alias_map({"frieren": "new canonical"}, "tmdb")
    assert await storage.find_alias_key("frieren") == "new canonical"
    rows = await _alias_rows(storage)
    assert rows["frieren"].source == "tmdb"
    assert len(rows) == 2


async def test_put_alias_map_skips_self_mappings(storage: SqliteStorage) -> None:
    await storage.put_alias_map(
        {CANONICAL_SHAPE: CANONICAL_SHAPE, "frieren": CANONICAL_SHAPE}, "bangumi"
    )
    rows = await _alias_rows(storage)
    assert set(rows) == {"frieren"}


async def test_title_alias_defaults_created_at(storage: SqliteStorage) -> None:
    before = datetime.now()
    await storage.put_alias_map({"frieren": CANONICAL_SHAPE}, "bangumi")
    row = (await _alias_rows(storage))["frieren"]
    assert row.created_at >= before


async def test_storage_memory_access_forwards_alias_methods(storage: SqliteStorage) -> None:
    """装配侧 StorageMemoryAccess 暴露读/写两个 alias 方法（后续接线用）。"""
    access = StorageMemoryAccess(storage)
    assert await access.find_alias_key("frieren") is None
    await access.put_alias_map({"frieren": CANONICAL_SHAPE}, "bangumi")
    assert await access.find_alias_key("frieren") == CANONICAL_SHAPE


# ---------------------------------------------------------------------------
# alembic 0004：upgrade/downgrade
# ---------------------------------------------------------------------------


def test_migration_0004_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "migration.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(config, "0004")
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info('title_aliases')")
        }
        pk_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('title_aliases')")
            if row[5]  # pk 字段非 0 即主键列
        }
    assert "title_aliases" in tables
    assert columns == {"title_shape_norm", "canonical_shape", "source", "created_at"}
    assert pk_columns == {"title_shape_norm"}

    command.downgrade(config, "0003")
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "title_aliases" not in tables


def test_alias_backfill_timeout_is_strict() -> None:
    """护栏契约：miss 外呼的超时上限常量为 3 秒。"""
    assert ALIAS_BACKFILL_TIMEOUT_S <= 3.0
