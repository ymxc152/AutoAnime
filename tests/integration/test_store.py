from __future__ import annotations

from autoanime.core.interfaces import Storage
from autoanime.core.models import Series
from autoanime.memory.store import SqliteStorage


async def test_sqlite_storage_crud() -> None:
    store = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await store.create_all()
    try:
        assert isinstance(store, Storage)
        series = Series(title_cn="Test")
        await store.add(series)
        assert series.id is not None

        fetched = await store.get(Series, series.id)
        assert fetched is not None
        assert fetched.title_cn == "Test"

        items = await store.list(Series)
        assert len(items) == 1

        await store.delete(fetched)
        assert await store.get(Series, series.id) is None
    finally:
        await store.close()
