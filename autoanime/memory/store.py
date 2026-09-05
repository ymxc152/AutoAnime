from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from autoanime.core.models import Alias, Base, BypassList, ParseMemory


class SqliteStorage:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._is_memory = ":memory:" in database_url
        engine_kwargs: dict[str, Any] = {}
        if self._is_memory:
            engine_kwargs["poolclass"] = StaticPool
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        self._engine = create_async_engine(database_url, **engine_kwargs)

        def _configure_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=5000")
            if not self._is_memory:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        event.listen(self._engine.sync_engine, "connect", _configure_pragma)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Run a group of writes in one SQLite transaction."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def add(self, obj: Any) -> None:
        async with self._session_factory() as session:
            session.add(obj)
            await session.commit()

    async def get(self, model: type[Any], id_: int) -> Any | None:
        async with self._session_factory() as session:
            return await session.get(model, id_)

    async def list(self, model: type[Any]) -> list[Any]:
        async with self._session_factory() as session:
            result = await session.execute(select(model))
            return list(result.scalars().all())

    async def find_parse_memory(self, key_level: int, key_hash: str) -> ParseMemory | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ParseMemory).where(
                    ParseMemory.key_level == key_level,
                    ParseMemory.key_hash == key_hash,
                )
            )
            return result.scalar_one_or_none()

    async def find_bypass(self, pattern_hash: str) -> BypassList | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(BypassList).where(BypassList.pattern_hash == pattern_hash)
            )
            return result.scalar_one_or_none()

    async def find_aliases_by_norm(self, alias_norm: str) -> list[Alias]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Alias).where(Alias.alias_norm == alias_norm).order_by(Alias.id)
            )
            return list(result.scalars().all())

    async def find_aliases_by_series(self, series_id: int) -> list[Alias]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Alias).where(Alias.series_id == series_id).order_by(Alias.id)
            )
            return list(result.scalars().all())

    async def delete(self, obj: Any) -> None:
        async with self._session_factory() as session:
            obj = await session.merge(obj)
            await session.delete(obj)
            await session.commit()

    async def close(self) -> None:
        await self._engine.dispose()

    async def __aenter__(self) -> SqliteStorage:
        await self.create_all()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()
