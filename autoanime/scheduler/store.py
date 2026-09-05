"""订阅闭环的 store 层（E4）：调度器/CLI 手动触发共用的唯一 DB 会话边界。

铁律 3「DB 会话只在 store 层」：本模块是 scheduler 域的 store 收口（与
E2 的 ``web.queries.ApiStore`` 同一模式），所有 SQLAlchemy 会话只在
``SqliteStorage.transaction()`` 内开。CLI ``subscribe``/``rerun`` 与
AsyncIOScheduler 任务走同一批方法（审核 A7：并发写库收口 + 状态机守卫 +
torrent_hash 唯一约束兜底）。

状态机守卫：episode / release_record 的每次转移都先过
``can_transition``，非法转移直接拒绝（返回 False / 抛 ``TransitionError``），
不静默改写。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from autoanime.core.enums import (
    Decision,
    EpisodeState,
    PendingStatus,
    ReleaseStatus,
    SeasonState,
)
from autoanime.core.models import (
    Episode,
    PendingQueue,
    ReleaseRecord,
    RssSource,
    Season,
    Series,
)
from autoanime.memory.store import SqliteStorage


class TransitionError(Exception):
    """非法状态机转移（A7：直接拒绝，不静默）。"""


class LoopStore:
    """订阅闭环的 DB 读写收口（会话只在内部）。"""

    def __init__(self, storage: SqliteStorage) -> None:
        self._storage = storage

    # --- subscription creation（CLI subscribe / 调度共用入口） ---------------

    async def create_subscription(
        self,
        series: Series,
        season: Season,
        episodes: list[Episode],
        *,
        season_status: SeasonState = SeasonState.UPCOMING,
    ) -> Series:
        """Series + Season + 预生成 Episode 行落一个事务（与 web 同语义）。

        ``season_status``：CLI subscribe 追更即刻开始 → AIRING（CLI 显式
        构造 Season(status=AIRING)）；此处只在调用方未设季状态时兜底
        UPCOMING，不覆盖已设值（poller 测试/多季订阅各自控制）。
        """
        if season.status is None:
            season.status = season_status
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

    async def add_rss_source(self, row: RssSource) -> RssSource:
        async with self._storage.transaction() as session:
            session.add(row)
            await session.flush()
        return row

    # --- read sides ----------------------------------------------------------

    async def enabled_rss_sources(self) -> list[RssSource]:
        async with self._storage.transaction() as session:
            rows = (
                await session.execute(
                    select(RssSource).where(RssSource.enabled.is_(True)).order_by(RssSource.id)
                )
            ).scalars().all()
        return list(rows)

    async def get_rss_source(self, source_id: int) -> RssSource | None:
        async with self._storage.transaction() as session:
            return await session.get(RssSource, source_id)

    async def season_series(self, season_id: int) -> tuple[Season, Series] | None:
        async with self._storage.transaction() as session:
            season = await session.get(Season, season_id)
            if season is None:
                return None
            series = await session.get(Series, season.series_id)
            if series is None:
                return None
        return season, series

    async def episodes_for_season(self, season_id: int) -> list[Episode]:
        async with self._storage.transaction() as session:
            rows = (
                await session.execute(
                    select(Episode)
                    .where(Episode.season_id == season_id)
                    .order_by(Episode.number)
                )
            ).scalars().all()
        return list(rows)

    async def episode_for_number(self, season_id: int, number: int) -> Episode | None:
        async with self._storage.transaction() as session:
            row = (
                await session.execute(
                    select(Episode)
                    .where(Episode.season_id == season_id, Episode.number == number)
                )
            ).scalar_one_or_none()
        return row

    async def get_episode(self, episode_id: int) -> Episode | None:
        async with self._storage.transaction() as session:
            return await session.get(Episode, episode_id)

    async def mark_polled(self, source_id: int, at: datetime) -> None:
        async with self._storage.transaction() as session:
            row = await session.get(RssSource, source_id)
            if row is None:
                return
            row.last_polled_at = at
            session.add(row)

    # --- release_record（expected 权威载体） -----------------------------------

    async def find_release_by_hash(self, torrent_hash: str) -> ReleaseRecord | None:
        async with self._storage.transaction() as session:
            row = (
                await session.execute(
                    select(ReleaseRecord).where(ReleaseRecord.torrent_hash == torrent_hash)
                )
            ).scalar_one_or_none()
        return row

    async def find_release_by_source_url(self, source_url: str) -> ReleaseRecord | None:
        async with self._storage.transaction() as session:
            row = (
                await session.execute(
                    select(ReleaseRecord).where(ReleaseRecord.source_url == source_url)
                )
            ).scalar_one_or_none()
        return row

    async def find_releases_by_episode(self, episode_id: int) -> list[ReleaseRecord]:
        async with self._storage.transaction() as session:
            rows = (
                await session.execute(
                    select(ReleaseRecord).where(ReleaseRecord.episode_id == episode_id)
                )
            ).scalars().all()
        return list(rows)

    async def list_releases_by_status(
        self, statuses: Sequence[ReleaseStatus]
    ) -> list[ReleaseRecord]:
        async with self._storage.transaction() as session:
            rows = (
                await session.execute(
                    select(ReleaseRecord)
                    .where(ReleaseRecord.status.in_([s.value for s in statuses]))
                    .order_by(ReleaseRecord.id)
                )
            ).scalars().all()
        return list(rows)

    async def create_release(self, record: ReleaseRecord) -> ReleaseRecord | None:
        """新建候选；torrent_hash 唯一约束兜底（撞哈希返回 None = seen 去重）。"""
        async with self._storage.transaction() as session:
            session.add(record)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                return None
        return record

    async def transition_release(
        self,
        record_id: int,
        target: ReleaseStatus,
        *,
        now: datetime,
        decision: Decision | None = None,
        reason: str | None = None,
    ) -> ReleaseRecord | None:
        """带守卫的状态转移；非法转移抛 ``TransitionError``（A7）。"""
        async with self._storage.transaction() as session:
            row = await session.get(ReleaseRecord, record_id)
            if row is None:
                return None
            current = ReleaseStatus(row.status.value if hasattr(row.status, "value") else row.status)
            if current is target:
                return row
            if not current.can_transition(target):
                raise TransitionError(f"release {record_id}: {current} -> {target} illegal")
            row.status = target
            if target is ReleaseStatus.PICKED:
                row.picked_at = now
            if target in (ReleaseStatus.COMPLETED, ReleaseStatus.FAILED):
                row.finished_at = now
            if decision is not None:
                row.decision = decision
            if reason is not None:
                row.reason = reason
            session.add(row)
        return row

    # --- episode 状态机 ---------------------------------------------------------

    async def transition_episode(
        self, episode_id: int, target: EpisodeState
    ) -> Episode | None:
        """带守卫的集状态转移；非法转移抛 ``TransitionError``。"""
        async with self._storage.transaction() as session:
            row = await session.get(Episode, episode_id)
            if row is None:
                return None
            current = EpisodeState(row.state.value if hasattr(row.state, "value") else row.state)
            if current is target:
                return row
            if not current.can_transition(target):
                raise TransitionError(f"episode {episode_id}: {current} -> {target} illegal")
            row.state = target
            session.add(row)
        return row

    async def set_episode_file(self, episode_id: int, *, file_path: str | None) -> Episode | None:
        """归档/回滚时维护文件指针（organize 与对账共用）。"""
        async with self._storage.transaction() as session:
            row = await session.get(Episode, episode_id)
            if row is None:
                return None
            row.file_path = file_path
            session.add(row)
        return row

    # --- ORGANIZED 行（B5 对账输入） ---------------------------------------------

    async def organized_episodes(self) -> list[Episode]:
        async with self._storage.transaction() as session:
            rows = (
                await session.execute(
                    select(Episode).where(
                        Episode.state == EpisodeState.ORGANIZED.value,
                        Episode.file_path.is_not(None),
                    )
                )
            ).scalars().all()
        return list(rows)

    # --- E4b：归档服务 / 错配恢复 / B5 对账所需 --------------------------------

    async def episode_context(self, episode_id: int) -> tuple[Episode, Season, Series] | None:
        """归档所需的完整上下文（episode → season → series，一次取齐）。"""
        async with self._storage.transaction() as session:
            episode = await session.get(Episode, episode_id)
            if episode is None:
                return None
            season = await session.get(Season, episode.season_id) if episode.season_id else None
            series = await session.get(Series, episode.series_id)
            if season is None or series is None:
                return None
        return episode, season, series

    async def update_episode_archive_state(
        self,
        episode_id: int,
        *,
        target: EpisodeState,
        file_path: str | None = None,
        quality_score: float | None = None,
        upgraded_count_delta: int = 0,
    ) -> Episode | None:
        """归档/洗版落地：状态转移 + 文件指针 + 质量分 + 洗版计数（同事务）。"""
        async with self._storage.transaction() as session:
            row = await session.get(Episode, episode_id)
            if row is None:
                return None
            current = EpisodeState(
                row.state.value if hasattr(row.state, "value") else row.state
            )
            if current is not target and not current.can_transition(target):
                raise TransitionError(f"episode {episode_id}: {current} -> {target} illegal")
            row.state = target
            if file_path is not None:
                row.file_path = file_path
            if quality_score is not None:
                row.quality_score = quality_score
            if upgraded_count_delta:
                row.upgraded_count = int(row.upgraded_count or 0) + upgraded_count_delta
            session.add(row)
        return row

    async def set_release_episode(self, record_id: int, episode_id: int) -> ReleaseRecord | None:
        """错配恢复 A（改挂）：release_record 换挂目标集（expected 载体更新）。"""
        async with self._storage.transaction() as session:
            row = await session.get(ReleaseRecord, record_id)
            if row is None:
                return None
            row.episode_id = episode_id
            row.season_id = None
            session.add(row)
        return row

    async def set_release_decision(
        self, record_id: int, decision: Decision, *, reason: str | None = None
    ) -> ReleaseRecord | None:
        """更新已终态 release 的 decision/reason（不改生命周期状态）。"""
        async with self._storage.transaction() as session:
            row = await session.get(ReleaseRecord, record_id)
            if row is None:
                return None
            row.decision = decision
            if reason is not None:
                row.reason = reason
            session.add(row)
        return row

    async def add_pending(self, row: PendingQueue) -> PendingQueue:
        async with self._storage.transaction() as session:
            session.add(row)
            await session.flush()
        return row

    async def count_pending(self) -> int:
        async with self._storage.transaction() as session:
            return int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(PendingQueue)
                        .where(PendingQueue.status == PendingStatus.PENDING.value)
                    )
                ).scalar_one()
            )

    async def seasons_by_ids(self, season_ids: Sequence[int]) -> list[Season]:
        if not season_ids:
            return []
        async with self._storage.transaction() as session:
            rows = (
                await session.execute(
                    select(Season).where(Season.id.in_(season_ids)).order_by(Season.id)
                )
            ).scalars().all()
        return list(rows)

    async def seasons_for_series(self, series_id: int) -> list[Season]:
        async with self._storage.transaction() as session:
            rows = (
                await session.execute(
                    select(Season)
                    .where(Season.series_id == series_id)
                    .order_by(Season.number)
                )
            ).scalars().all()
        return list(rows)
