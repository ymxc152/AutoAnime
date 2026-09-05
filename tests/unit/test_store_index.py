"""Store 层 DB 级查询与查询索引（PR5 T2）。

PR4 遗留的 ``find_parse_memory`` / ``has_bypass`` 全表 Python 过滤已由
commit 6bfe494 改为 store 层 ``SELECT ... WHERE`` 谓词（``SqliteStorage``
的 ``find_parse_memory`` / ``find_bypass``，lookup.py / learn.py 均已改调）。
本文件覆盖剩余部分：

- ``create_all``（及 alembic 0002）为 ``parse_memory`` / ``bypass_list``
  建立查询索引，DB 级谓词不再退化为全表扫描；
- 查询语义回归：谓词按 (key_level, key_hash) / pattern_hash 精确匹配，
  行为与 6bfe494 之前（Python 过滤时代）的既有单测完全兼容。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from autoanime.core.models import BypassList, ParseMemory
from autoanime.memory.store import SqliteStorage


async def _collect_indexes(db_path: Path) -> dict[str, set[str]]:
    """create_all 后用独立连接读 SQLite 索引清单。"""
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    store = SqliteStorage(url)
    await store.create_all()
    await store.close()

    engine = create_async_engine(url)
    try:

        def _read(sync_connection: object) -> dict[str, set[str]]:
            inspector = inspect(sync_connection)
            return {
                table: {ix["name"] for ix in inspector.get_indexes(table)}
                for table in ("parse_memory", "bypass_list")
            }

        async with engine.connect() as connection:
            return await connection.run_sync(_read)
    finally:
        await engine.dispose()


async def test_create_all_builds_lookup_indexes(tmp_path: Path) -> None:
    indexes = await _collect_indexes(tmp_path / "idx.db")
    assert "ix_parse_memory_key_level_hash" in indexes["parse_memory"]
    assert "ix_bypass_list_pattern_hash" in indexes["bypass_list"]


async def test_find_parse_memory_matches_exact_key_pair() -> None:
    store = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await store.create_all()
    try:
        exact = ParseMemory(key_level=2, key_hash="h-exact", result={})
        series = ParseMemory(key_level=1, key_hash="h-series", result={})
        same_level = ParseMemory(key_level=2, key_hash="h-other", result={})
        for row in (exact, series, same_level):
            await store.add(row)

        found = await store.find_parse_memory(2, "h-exact")
        assert found is not None and found.id == exact.id
        # key_level 参与匹配：同 hash 不同 level 不可命中。
        assert await store.find_parse_memory(1, "h-exact") is None
        assert await store.find_parse_memory(2, "missing") is None
    finally:
        await store.close()


async def test_find_bypass_matches_exact_pattern_hash() -> None:
    store = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await store.create_all()
    try:
        listed = BypassList(pattern_hash="h-listed", reason="user")
        await store.add(listed)

        found = await store.find_bypass("h-listed")
        assert found is not None and found.id == listed.id
        assert await store.find_bypass("h-missing") is None
    finally:
        await store.close()
