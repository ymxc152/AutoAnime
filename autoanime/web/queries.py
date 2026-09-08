"""Web 层数据访问（E2）：API 读侧查询 + 少量事务写。

边界说明（铁律 3「DB 会话只在 store 层」）：本模块是 API 的 store 层——
所有 SQLAlchemy 会话只在 ``ApiStore`` 内部开启，且统一经由
``SqliteStorage.transaction()``（memory/store.py 因 E1 并行开发冻结，
本模块作为 web 域的 store 收口，不绕过既有 store 直接造引擎）。
路由层只拿本模块与 memory 层的既有方法，不触碰 session。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import BigInteger, delete, func, or_, select

from autoanime.core.enums import MediaType, MemoryStatus, PendingStatus, ResolvedBy
from autoanime.core.models import (
    AuditLog,
    Episode,
    ParseEvents,
    ParseMemory,
    PendingQueue,
    RssSource,
    Season,
    Series,
)
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l2.placeholders import build_title_shape


@dataclass(frozen=True)
class OperationGroup:
    """audit 按 operation_id 聚合的一组（Logs 页时间线）。"""

    operation_id: str
    rows: int
    entities: list[str]
    actions: list[str]
    first_audit_id: int
    last_audit_id: int
    #: Logs UI 只对「组内最新行有 reverse 指令」的组开放撤销入口。
    rollbackable: bool


@dataclass
class MetricsSnapshot:
    """metrics 聚合的中间结果（纯数据，由路由层映射为 schema）。"""

    audit_total: int
    audit_manual: int
    by_level: list[dict[str, object]] = field(default_factory=list)
    daily_parse_events: dict[date, tuple[int, int]] = field(default_factory=dict)
    daily_pending_created: dict[date, int] = field(default_factory=dict)
    daily_pending_resolved: dict[date, int] = field(default_factory=dict)
    pending_open: int = 0
    episode_states: dict[str, int] = field(default_factory=dict)
    memory_sources: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class SubscriptionUpsertResult:
    """P0-B adopt 建订阅的结果：series 行 + 是否收编已有番 + RSS 是否落库。"""

    series: Series
    adopted: bool
    rss_saved: bool


def _enum_value(value: MediaType | str) -> str:
    return value.value if isinstance(value, MediaType) else str(value)


def _title_shapes(*titles: str | None) -> set[str]:
    """非空标题的归一化 shape 集合（P0-B adopt 查重键）。"""
    return {build_title_shape(title) for title in titles if title}


class ApiStore:
    """API 数据访问收口：会话只在 ``SqliteStorage.transaction()`` 内。"""

    def __init__(self, storage: SqliteStorage) -> None:
        self._storage = storage

    # --- Library：series/season/episode 树 ---------------------------------

    async def list_series_page(
        self, *, limit: int, offset: int, q: str | None = None
    ) -> tuple[list[Series], int]:
        async with self._storage.transaction() as session:
            stmt = select(Series)
            count_stmt = select(func.count()).select_from(Series)
            needle = q.strip() if q is not None else ''
            if needle:
                condition = or_(
                    Series.title_cn.ilike(f"%{needle}%"),
                    Series.title_jp.ilike(f"%{needle}%"),
                    Series.title_romaji.ilike(f"%{needle}%"),
                )
                stmt = stmt.where(condition)
                count_stmt = count_stmt.where(condition)
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (
                (await session.execute(
                    stmt.order_by(Series.id).limit(limit).offset(offset)
                )).scalars().all()
            )
        return list(rows), int(total)

    async def get_series(self, series_id: int) -> Series | None:
        async with self._storage.transaction() as session:
            return await session.get(Series, series_id)

    async def seasons_for(self, series_ids: Sequence[int]) -> list[Season]:
        if not series_ids:
            return []
        async with self._storage.transaction() as session:
            rows = (
                (await session.execute(
                    select(Season)
                    .where(Season.series_id.in_(series_ids))
                    .order_by(Season.series_id, Season.number)
                )).scalars().all()
            )
        return list(rows)

    async def episodes_for(self, series_ids: Sequence[int]) -> list[Episode]:
        if not series_ids:
            return []
        async with self._storage.transaction() as session:
            rows = (
                (await session.execute(
                    select(Episode)
                    .where(Episode.series_id.in_(series_ids))
                    .order_by(Episode.series_id, Episode.number)
                )).scalars().all()
            )
        return list(rows)

    # --- Pending ------------------------------------------------------------

    async def list_pending_page(
        self, *, status: str | None, limit: int, offset: int
    ) -> tuple[list[PendingQueue], int]:
        async with self._storage.transaction() as session:
            query = select(PendingQueue).order_by(PendingQueue.id.desc())
            count_query = select(func.count()).select_from(PendingQueue)
            if status is not None:
                query = query.where(PendingQueue.status == status)
                count_query = count_query.where(PendingQueue.status == status)
            total = (await session.execute(count_query)).scalar_one()
            rows = (await session.execute(query.limit(limit).offset(offset))).scalars().all()
        return list(rows), int(total)

    async def get_pending(self, pending_id: int) -> PendingQueue | None:
        async with self._storage.transaction() as session:
            return await session.get(PendingQueue, pending_id)

    async def resolve_pending(
        self,
        pending_id: int,
        *,
        status: PendingStatus,
        resolution: dict[str, object] | None,
        resolved_by: ResolvedBy,
        audit_row: AuditLog | None = None,
    ) -> AuditLog | None:
        """一次性落：pending 行状态 + 审计行（同一事务）。返回带 id 的审计行。

        ``pending_queue.resolution`` 列是 String（models.py 本任务冻结不改），
        dict 载荷由本层序列化为 JSON 字符串落库，读取侧（PendingOut）解析还原。
        """
        async with self._storage.transaction() as session:
            row = await session.get(PendingQueue, pending_id)
            if row is None:
                return None
            row.status = status
            row.resolution = (
                json.dumps(resolution, ensure_ascii=False) if resolution is not None else None
            )
            row.resolved_by = resolved_by
            row.resolved_at = datetime.now()
            session.add(row)
            if audit_row is not None:
                session.add(audit_row)
                await session.flush()
        return audit_row

    # --- Audit ---------------------------------------------------------------

    async def list_audit_page(
        self,
        *,
        operation_id: str | None,
        entity: str | None,
        action: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AuditLog], int]:
        async with self._storage.transaction() as session:
            conditions = []
            if operation_id is not None:
                conditions.append(AuditLog.operation_id == operation_id)
            if entity is not None:
                conditions.append(AuditLog.entity == entity)
            if action is not None:
                conditions.append(AuditLog.action == action)
            query = select(AuditLog).order_by(AuditLog.id.desc())
            count_query = select(func.count()).select_from(AuditLog)
            if conditions:
                query = query.where(*conditions)
                count_query = count_query.where(*conditions)
            total = (await session.execute(count_query)).scalar_one()
            rows = (await session.execute(query.limit(limit).offset(offset))).scalars().all()
        return list(rows), int(total)

    async def list_audit_operations(
        self, *, limit: int, offset: int
    ) -> tuple[list[OperationGroup], int]:
        """按 operation_id 分组：组内行数/实体/动作 + 首末审计 id，最新组在前。"""
        async with self._storage.transaction() as session:
            rows = (
                (await session.execute(
                    select(AuditLog).order_by(AuditLog.operation_id, AuditLog.id)
                )).scalars().all()
            )
        groups: dict[str, list[AuditLog]] = {}
        for row in rows:
            groups.setdefault(row.operation_id, []).append(row)
        ordered = sorted(groups.items(), key=lambda item: item[1][0].id, reverse=True)
        total = len(ordered)
        page = ordered[offset : offset + limit]
        return [
            OperationGroup(
                operation_id=operation_id,
                rows=len(rows_),
                entities=sorted({row.entity for row in rows_}),
                actions=sorted({row.action for row in rows_}),
                first_audit_id=rows_[0].id,
                last_audit_id=rows_[-1].id,
                rollbackable=bool(rows_[-1].reverse or {}),
            )
            for operation_id, rows_ in page
        ], total

    async def add_audit_row(self, row: AuditLog) -> AuditLog:
        """写一条审计行并带回 id（供 SSE 事件 payload 使用）。"""
        async with self._storage.transaction() as session:
            session.add(row)
            await session.flush()
        return row

    async def get_audit(self, audit_id: int) -> AuditLog | None:
        async with self._storage.transaction() as session:
            return await session.get(AuditLog, audit_id)

    async def restore_parse_memory_status(self, entity_id: int, status_value: str) -> bool:
        """回滚执行引擎 v1 分支：把 parse_memory 行状态恢复为 reverse 记录值。

        仅接受合法 ``MemoryStatus`` 值；行不存在或值非法返回 ``False``。
        organize 文件反操作等其余实体由 E4 的 mover/rollback 落地后扩展。
        """
        try:
            target = MemoryStatus(status_value)
        except ValueError:
            return False
        async with self._storage.transaction() as session:
            row = await session.get(ParseMemory, entity_id)
            if row is None:
                return False
            row.status = target
            session.add(row)
        return True

    # --- SSE 回放 -------------------------------------------------------------

    async def replay_after(self, after_id: int, limit: int) -> list[AuditLog]:
        """``Last-Event-ID`` 重放：id 大于该值的最早 N 条（升序补发）。"""
        async with self._storage.transaction() as session:
            rows = (
                (await session.execute(
                    select(AuditLog)
                    .where(AuditLog.id > after_id)
                    .order_by(AuditLog.id)
                    .limit(limit)
                )).scalars().all()
            )
        return list(rows)

    async def replay_recent(self, limit: int) -> list[AuditLog]:
        """显式 ``replay=N``：最近 N 条（按 id 升序回放）。"""
        async with self._storage.transaction() as session:
            latest = (
                (await session.execute(
                    select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
                )).scalars().all()
            )
        return list(reversed(list(latest)))

    # --- Subscriptions ---------------------------------------------------------

    async def create_subscription(
        self, series: Series, season: Season, episodes: list[Episode]
    ) -> Series:
        async with self._storage.transaction() as session:
            session.add(series)
            await session.flush()
            season.series_id = series.id
            session.add(season)
            await session.flush()
            for episode in episodes:
                episode.series_id = series.id
                episode.season_id = season.id
            session.add_all(episodes)
        return series

    async def create_or_adopt_subscription(
        self,
        series: Series,
        season: Season,
        episodes: list[Episode],
        *,
        rss_url: str | None = None,
        rss_token: str | None = None,
    ) -> SubscriptionUpsertResult:
        """P0-B adopt 建订阅（一个事务）：已有番收编，未命中走原 create。

        查重：``Series.bangumi_id`` 精确匹配优先；否则 ``build_title_shape``
        归一化标题与全量 Series 行内存比对（单用户库量小，全量可接受），
        ``media_type`` 参与比对（剧场版 vs TV 同题不合并）。

        命中（adopt 消幻影）：status 置回 active、回填空标题/bangumi_id、
        find_or_create Season、**只为缺失集号补 MISSING 行**（已有
        ORGANIZED/UPGRADED 等 import 归档行原样保留，绝不整季重生成）。
        未命中：与 ``create_subscription`` 同语义落 series/season/episodes。

        ``rss_url`` 提供时在同一事务内挂 RssSource（season_id 用新建/命中
        的季；同季同 URL 已存在则不重复建）。token 只落 DB，调用端不回显。
        """
        async with self._storage.transaction() as session:
            existing: Series | None = None
            if series.bangumi_id:
                existing = (
                    await session.execute(
                        select(Series).where(Series.bangumi_id == series.bangumi_id)
                    )
                ).scalars().first()
            if existing is None:
                incoming = _title_shapes(series.title_cn, series.title_jp, series.title_romaji)
                if incoming:
                    for row in (
                        await session.execute(select(Series).order_by(Series.id))
                    ).scalars():
                        if _enum_value(row.media_type) != _enum_value(series.media_type):
                            continue
                        if _title_shapes(
                            row.title_cn, row.title_jp, row.title_romaji
                        ) & incoming:
                            existing = row
                            break

            rss_saved = False
            if existing is not None:
                # adopt：置回 active + 回填空槽，不覆盖已有偏好/标题。
                existing.status = "active"
                for slot in ("title_cn", "title_jp", "title_romaji"):
                    if getattr(existing, slot) is None and getattr(series, slot) is not None:
                        setattr(existing, slot, getattr(series, slot))
                if existing.bangumi_id is None and series.bangumi_id:
                    existing.bangumi_id = series.bangumi_id
                session.add(existing)
                await session.flush()
                season_row = (
                    await session.execute(
                        select(Season).where(
                            Season.series_id == existing.id,
                            Season.number == season.number,
                        )
                    )
                ).scalars().first()
                if season_row is None:
                    season.series_id = existing.id
                    session.add(season)
                    await session.flush()
                    season_row = season
                existing_numbers = set(
                    (
                        await session.execute(
                            select(Episode.number).where(Episode.season_id == season_row.id)
                        )
                    ).scalars().all()
                )
                for episode in episodes:
                    if episode.number in existing_numbers:
                        continue  # 已有行（含 ORGANIZED 归档行）原样保留
                    episode.series_id = existing.id
                    episode.season_id = season_row.id
                    session.add(episode)
                target: Series = existing
                target_season_id = season_row.id
                adopted = True
            else:
                session.add(series)
                await session.flush()
                season.series_id = series.id
                session.add(season)
                await session.flush()
                for episode in episodes:
                    episode.series_id = series.id
                    episode.season_id = season.id
                session.add_all(episodes)
                target = series
                target_season_id = season.id
                adopted = False

            if rss_url:
                duplicate = (
                    await session.execute(
                        select(RssSource).where(
                            RssSource.season_id == target_season_id,
                            RssSource.url == rss_url,
                        )
                    )
                ).scalars().first()
                if duplicate is None:
                    session.add(
                        RssSource(
                            url=rss_url, token=rss_token, season_id=target_season_id
                        )
                    )
                    rss_saved = True
        return SubscriptionUpsertResult(series=target, adopted=adopted, rss_saved=rss_saved)

    async def update_series_fields(
        self, series_id: int, fields: dict[str, object]
    ) -> Series | None:
        async with self._storage.transaction() as session:
            row = await session.get(Series, series_id)
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            session.add(row)
        return row

    async def delete_subscription(self, series_id: int) -> bool:
        """删除订阅 = 同一事务内级联清 rss_source/episode/season + series。"""
        async with self._storage.transaction() as session:
            row = await session.get(Series, series_id)
            if row is None:
                return False
            await session.execute(
                delete(RssSource).where(
                    RssSource.season_id.in_(select(Season.id).where(Season.series_id == series_id))
                )
            )
            await session.execute(delete(Episode).where(Episode.series_id == series_id))
            await session.execute(delete(Season).where(Season.series_id == series_id))
            await session.delete(row)
        return True

    async def season_rss_counts(self, season_ids: Sequence[int]) -> dict[int, int]:
        if not season_ids:
            return {}
        async with self._storage.transaction() as session:
            rows = (
                await session.execute(
                    select(RssSource.season_id, func.count())
                    .where(RssSource.season_id.in_(season_ids))
                    .group_by(RssSource.season_id)
                )
            ).all()
        return {int(season_id): int(count) for season_id, count in rows}

    # --- RSS sources（B3） ------------------------------------------------------

    async def season_exists(self, season_id: int) -> bool:
        async with self._storage.transaction() as session:
            return await session.get(Season, season_id) is not None

    async def list_rss_sources_page(
        self, *, limit: int, offset: int
    ) -> tuple[list[RssSource], int]:
        async with self._storage.transaction() as session:
            total = (
                await session.execute(select(func.count()).select_from(RssSource))
            ).scalar_one()
            rows = (
                (await session.execute(
                    select(RssSource).order_by(RssSource.id).limit(limit).offset(offset)
                )).scalars().all()
            )
        return list(rows), int(total)

    async def get_rss_source(self, source_id: int) -> RssSource | None:
        async with self._storage.transaction() as session:
            return await session.get(RssSource, source_id)

    async def add_rss_source(self, row: RssSource) -> RssSource:
        async with self._storage.transaction() as session:
            session.add(row)
            await session.flush()
        return row

    async def update_rss_source(
        self, source_id: int, fields: dict[str, object]
    ) -> RssSource | None:
        async with self._storage.transaction() as session:
            row = await session.get(RssSource, source_id)
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            session.add(row)
        return row

    async def delete_rss_source(self, source_id: int) -> bool:
        async with self._storage.transaction() as session:
            row = await session.get(RssSource, source_id)
            if row is None:
                return False
            await session.delete(row)
        return True

    # --- Metrics 聚合 -----------------------------------------------------------

    async def metrics_snapshot(self, *, trend_days: int) -> MetricsSnapshot:
        since_date = date.today() - timedelta(days=trend_days - 1)
        llm_sum = func.coalesce(func.sum(func.cast(ParseEvents.llm_called, BigInteger)), 0)
        async with self._storage.transaction() as session:
            audit_total = (
                await session.execute(select(func.count()).select_from(AuditLog))
            ).scalar_one()
            audit_manual = (
                await session.execute(
                    select(func.count()).select_from(AuditLog).where(AuditLog.actor == "manual")
                )
            ).scalar_one()

            level_rows = (
                await session.execute(
                    select(ParseEvents.level, ParseEvents.outcome, func.count(), llm_sum).group_by(
                        ParseEvents.level, ParseEvents.outcome
                    )
                )
            ).all()

            daily_rows = (
                await session.execute(
                    select(ParseEvents.event_date, func.count(), llm_sum)
                    .where(ParseEvents.event_date >= since_date)
                    .group_by(ParseEvents.event_date)
                )
            ).all()

            created_rows = (
                await session.execute(
                    select(func.date(PendingQueue.created_at), func.count())
                    .where(func.date(PendingQueue.created_at) >= since_date.isoformat())
                    .group_by(func.date(PendingQueue.created_at))
                )
            ).all()
            resolved_rows = (
                await session.execute(
                    select(func.date(PendingQueue.resolved_at), func.count())
                    .where(PendingQueue.resolved_at.is_not(None))
                    .where(func.date(PendingQueue.resolved_at) >= since_date.isoformat())
                    .group_by(func.date(PendingQueue.resolved_at))
                )
            ).all()
            pending_open = (
                await session.execute(
                    select(func.count())
                    .select_from(PendingQueue)
                    .where(PendingQueue.status == PendingStatus.PENDING.value)
                )
            ).scalar_one()

            state_rows = (
                await session.execute(
                    select(Episode.state, func.count()).group_by(Episode.state)
                )
            ).all()
            memory_rows = (
                await session.execute(
                    select(ParseMemory.source, ParseMemory.status, func.count()).group_by(
                        ParseMemory.source, ParseMemory.status
                    )
                )
            ).all()

        snapshot = MetricsSnapshot(
            audit_total=int(audit_total),
            audit_manual=int(audit_manual),
            pending_open=int(pending_open),
        )
        by_level: dict[int, dict[str, object]] = {}
        for level, outcome, count, llm_called in level_rows:
            bucket = by_level.setdefault(
                int(level), {"level": int(level), "total": 0, "llm_called": 0, "outcomes": {}}
            )
            bucket["total"] = int(bucket["total"]) + int(count)  # type: ignore[assignment]
            bucket["llm_called"] = int(bucket["llm_called"]) + int(llm_called)  # type: ignore[assignment]
            bucket["outcomes"][str(outcome)] = int(count)  # type: ignore[index]
        snapshot.by_level = list(by_level.values())
        snapshot.daily_parse_events = {
            _as_date(day): (int(total), int(llm)) for day, total, llm in daily_rows
        }
        snapshot.daily_pending_created = {
            _as_date(day): int(count) for day, count in created_rows
        }
        snapshot.daily_pending_resolved = {
            _as_date(day): int(count) for day, count in resolved_rows
        }
        snapshot.episode_states = {str(state): int(count) for state, count in state_rows}
        snapshot.memory_sources = [
            {"source": str(source), "status": str(status), "rows": int(count)}
            for source, status, count in memory_rows
        ]
        return snapshot


def _as_date(value: object) -> date:
    """SQLite 的 ``func.date``/DATE 列在 aiosqlite 下可能回字符串，统一转 date。"""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
