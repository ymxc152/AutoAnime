from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from autoanime.core.models import (
    Alias,
    Base,
    BypassList,
    LlmCacheRow,
    ParseMemory,
    ReferenceCache,
    TitleAlias,
)
from autoanime.pipeline.l3.cache_key import LlmCache


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

    async def get_llm_cache(self, pattern_hash: str) -> LlmCache | None:
        """按 pattern_hash 精确读一条 LLM 缓存（PR5 LlmCacheStore 读语义）。

        只按键取回录制的响应原文，转换为 l3 的 ``LlmCache`` 数据类；
        响应解析失败按 miss 处理是调用方（T2 识别器）的职责，store 层
        无条件返回存储内容。未命中返回 ``None``。
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(LlmCacheRow).where(LlmCacheRow.pattern_hash == pattern_hash)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return LlmCache(
                pattern_hash=row.pattern_hash,
                response=row.response_text,
                model=row.model,
            )

    async def put_llm_cache(self, cache: LlmCache) -> None:
        """写一条 LLM 缓存（PR5 LlmCacheStore 写语义）。

        无脑存取：是否只缓存 schema 合法的真实调用响应由写侧（T3）
        保证，store 层不做校验。同一 pattern_hash 重复写入覆盖旧记录
        （每 pattern 至多一行）。
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(LlmCacheRow).where(LlmCacheRow.pattern_hash == cache.pattern_hash)
            )
            row = result.scalar_one_or_none()
            if row is None:
                session.add(
                    LlmCacheRow(
                        pattern_hash=cache.pattern_hash,
                        response_text=cache.response,
                        model=cache.model,
                    )
                )
            else:
                row.response_text = cache.response
                row.model = cache.model
            await session.commit()

    async def find_reference_cache(self, title_shape: str, provider: str) -> ReferenceCache | None:
        """按 ``(title_shape, provider)`` 精确读一条参考源缓存（PR6 P2）。

        无条件返回存储内容：是否过期、``facts`` JSON 是否可解析由调用方
        （``memory.reference_cache.CachedReference``）判定，store 层不解释
        语义。未命中返回 ``None``。
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(ReferenceCache).where(
                    ReferenceCache.title_shape == title_shape,
                    ReferenceCache.provider == provider,
                )
            )
            return result.scalar_one_or_none()

    async def add_reference_cache(self, row: ReferenceCache) -> None:
        """写一条参考源缓存（PR6 P2）。

        同一 ``(title_shape, provider)`` 重复写入覆盖旧记录（含正/负缓存
        互相覆盖）；每对至多一行，由 ``uq_reference_cache_shape_provider``
        约束兜底。
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(ReferenceCache).where(
                    ReferenceCache.title_shape == row.title_shape,
                    ReferenceCache.provider == row.provider,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                session.add(row)
            else:
                existing.facts = row.facts
                existing.fetched_at = row.fetched_at
                existing.expires_at = row.expires_at
            await session.commit()

    async def find_alias_key(self, title_shape_norm: str) -> str | None:
        """按 alias shape 读 canonical shape（PR7 M3 title_aliases 读侧）。

        返回 ``title_aliases`` 表中该形状对应的 ``canonical_shape``；未命中
        返回 ``None``。查询侧（M2）用它把任意语言变体零外呼归一到
        canonical 形状。
        """
        async with self._session_factory() as session:
            row = await session.get(TitleAlias, title_shape_norm)
            return row.canonical_shape if row is not None else None

    async def find_alias_row(
        self, title_shape_norm: str
    ) -> tuple[str, str | None] | None:
        """带 source 的 alias 读侧（A1' 确认名覆盖用）。

        返回 ``(canonical_shape, source)``；source 标记该映射是谁写的
        （``manual`` = 用户 confirm 时的草稿形状映射 / ``bangumi`` 等 =
        参考源回填）。orchestrator 仅对 ``manual`` 行触发确认名覆盖——
        覆盖必须可追溯到用户确认过的事实。
        """
        async with self._session_factory() as session:
            row = await session.get(TitleAlias, title_shape_norm)
            if row is None:
                return None
            return (row.canonical_shape, row.source)

    async def put_alias_map(self, mapping: dict[str, str], source: str) -> None:
        """幂等 upsert 一批「alias shape → canonical shape」映射（PR7 M3）。

        每个别形状至多一行（主键 ``title_shape_norm``，重复写入覆盖旧
        canonical）；``alias shape == canonical shape`` 的条目跳过不写
        （canonical 自身不是别名）。``source`` 记录 canonical 的参考源
        注册名（如 ``"bangumi"``）。
        """
        async with self._session_factory() as session:
            for alias_shape, canonical_shape in mapping.items():
                if alias_shape == canonical_shape:
                    continue
                await session.merge(
                    TitleAlias(
                        title_shape_norm=alias_shape,
                        canonical_shape=canonical_shape,
                        source=source,
                    )
                )
            await session.commit()

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


class StorageLlmCacheStore:
    """``LlmCacheStore``（PR5 T1 契约）在 ``SqliteStorage`` 上的适配器。

    Protocol 的 ``get``/``put`` 与 ``SqliteStorage`` 既有泛型 ``get`` /
    ``add`` 命名冲突，故具体读写落在 ``SqliteStorage.get_llm_cache`` /
    ``put_llm_cache``，本类只做签名适配；DB 会话仍只在 store 层内部。
    """

    def __init__(self, storage: SqliteStorage) -> None:
        self._storage = storage

    async def get(self, pattern_hash: str) -> LlmCache | None:
        return await self._storage.get_llm_cache(pattern_hash)

    async def put(self, cache: LlmCache) -> None:
        await self._storage.put_llm_cache(cache)
