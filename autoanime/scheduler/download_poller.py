"""下载轮询器（E4a）：进度轮询比对 / 完成回调 / 失败重试 / 启动补扫。

qBittorrent 无 webhook（审核 A4）：完成事件靠按 ``poll_interval`` 轮询
比对 ``state``/``progress``（判定纯函数见 gateway.qbittorrent）。
本模块职责（Plan §6 第 3 项）：

- ``poll_once``：PICKED/DOWNLOADING 的 release 逐个比对网关状态 →
  完成（COMPLETED + episode DOWNLOADED + 事件 + 完成回调钩子）/ 失败
  （重试 ≤ ``max_retries``：重新取种提交，进程内计数）/ 在下（picked →
  downloading）；
- ``reconcile_startup``（启动补扫）：状态 COMPLETED 但 episode 仍
  DOWNLOADING 的悬挂任务（进程在两次转移之间崩溃）补发 DOWNLOADED 转移
  与事件；「下载完成未归档」的归档动作由 organize 侧幂等接手（B4 语义）。

重试计数是进程内存态（重启清零）——v1 如实记录；跨重启重试进 backlog。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from autoanime.core.enums import EpisodeState, ReleaseStatus
from autoanime.core.events import Event, EventBus, EventCategory
from autoanime.core.models import ReleaseRecord
from autoanime.gateway import GatewayError
from autoanime.gateway.qbittorrent import is_completed, is_failed
from autoanime.scheduler.store import LoopStore, TransitionError

logger = logging.getLogger(__name__)

#: 完成回调签名：release + 网关文件清单（organize 侧在 E4b 接线）。
CompletedCallback = Callable[[ReleaseRecord, list[dict[str, object]]], Awaitable[Any]]


@dataclass
class DownloadPollReport:
    """一轮下载轮询汇总（处理过程中累加）。"""

    checked: int = 0
    completed: int = 0
    failed: int = 0
    retried: int = 0
    reconciled: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


class DownloadPoller:
    """下载任务轮询器：hash 幂等 + 状态机守卫 + 有限重试。"""

    def __init__(
        self,
        store: LoopStore,
        gateway: Any,
        *,
        bus: EventBus | None = None,
        max_retries: int = 2,
        on_completed: CompletedCallback | None = None,
        torrent_refetch: Callable[[str], Awaitable[bytes | None]] | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._bus = bus
        self._max_retries = max_retries
        self._on_completed = on_completed
        # 失败重试的取种通道（source_url → .torrent 字节）；未接线时失败直接
        # 落终态（诚实降级，不假装重试过）。
        self._torrent_refetch = torrent_refetch
        self._attempts: dict[str, int] = {}

    async def poll_once(self, *, now: datetime) -> DownloadPollReport:
        """对在途任务跑一轮比对（CLI rerun 与调度器共用入口，A7）。"""
        report = DownloadPollReport()
        releases = await self._store.list_releases_by_status(
            [ReleaseStatus.PICKED, ReleaseStatus.DOWNLOADING]
        )
        for release in releases:
            report.checked += 1
            try:
                await self._check(release, now=now, report=report)
            except GatewayError as exc:
                logger.warning("gateway error for %s: %s", release.torrent_hash, exc)
                report.notes = (*report.notes, f"gateway error: {release.torrent_hash}")
            except TransitionError as exc:
                logger.warning("transition refused: %s", exc)
                report.notes = (*report.notes, f"transition refused: {release.torrent_hash}")
        return report

    async def _check(self, release: ReleaseRecord, *, now: datetime, report: DownloadPollReport) -> None:
        status = await self._gateway_status(release.torrent_hash)
        if status is None:
            await self._handle_missing(release, now=now, report=report)
            return
        state = status.get("state")
        state_str = str(state) if state is not None else None
        progress_raw = status.get("progress")
        progress = float(progress_raw) if isinstance(progress_raw, (int, float)) else None
        if is_completed(state_str, progress):
            await self._complete(
                release, now=now, report=report,
                files=await self._gateway_files(release.torrent_hash),
            )
        elif is_failed(state_str):
            await self._retry_or_fail(release, now=now, report=report)
        elif self._release_status(release) is ReleaseStatus.PICKED:
            await self._store.transition_release(release.id, ReleaseStatus.DOWNLOADING, now=now)

    # ------------------------------------------------------------- transitions

    async def _complete(
        self,
        release: ReleaseRecord,
        *,
        now: datetime,
        report: DownloadPollReport,
        files: list[dict[str, object]] | None = None,
    ) -> None:
        await self._store.transition_release(release.id, ReleaseStatus.COMPLETED, now=now)
        episode_id = release.episode_id
        if episode_id is not None:
            try:
                await self._store.transition_episode(episode_id, EpisodeState.DOWNLOADED)
            except TransitionError:
                # 已 DOWNLOADED/ORGANIZED：重复完成事件（补扫/并发）幂等通过。
                logger.info("episode %s already past DOWNLOADED; idempotent skip", episode_id)
        report.completed += 1
        listing = files if files is not None else []
        await self._publish(
            EventCategory.DOWNLOAD,
            "download.completed",
            {
                "torrent_hash": release.torrent_hash,
                "episode_id": episode_id,
                "files": listing,
            },
        )
        if self._on_completed is not None and episode_id is not None:
            await self._on_completed(release, listing)

    async def _retry_or_fail(
        self, release: ReleaseRecord, *, now: datetime, report: DownloadPollReport
    ) -> None:
        """失败重试 ≤ max_retries：重新取种提交；超限/不可重试置 failed。"""
        torrent_hash = release.torrent_hash
        attempts = self._attempts.get(torrent_hash, 0)
        fail_reason: str | None = None
        if attempts >= self._max_retries:
            fail_reason = "downloader error, retries exhausted"
        elif release.source_url is None or self._torrent_refetch is None:
            fail_reason = "downloader error; torrent source unavailable for retry"
        else:
            data = await self._torrent_refetch(release.source_url)
            if data is None:
                fail_reason = "downloader error; torrent refetch failed"
        if fail_reason is not None:
            logger.warning("release %s failed permanently: %s", torrent_hash, fail_reason)
            await self._store.transition_release(
                release.id, ReleaseStatus.FAILED, now=now, reason=fail_reason
            )
            report.failed += 1
            await self._publish(
                EventCategory.ERROR,
                "download.failed",
                {"torrent_hash": torrent_hash, "episode_id": release.episode_id},
            )
            return
        add = self._gateway.add_torrent_bytes
        try:
            assert data is not None
            await add(data)
        except GatewayError as exc:
            logger.warning("retry add failed for %s: %s", torrent_hash, exc)
            await self._store.transition_release(
                release.id, ReleaseStatus.FAILED, now=now, reason=f"retry add: {exc}"
            )
            report.failed += 1
            return
        # 置 failed（合法转移）再回 picked，picked_at 刷新 = 本次重试时间。
        await self._store.transition_release(release.id, ReleaseStatus.FAILED, now=now)
        await self._store.transition_release(release.id, ReleaseStatus.PICKED, now=now)
        self._attempts[torrent_hash] = attempts + 1
        report.retried += 1
        logger.info("release %s retry %s/%s", torrent_hash, attempts + 1, self._max_retries)

    async def _handle_missing(
        self, release: ReleaseRecord, *, now: datetime, report: DownloadPollReport
    ) -> None:
        """网关查无此任务（qB 重启/被清理）：同失败重试路径。"""
        await self._retry_or_fail(release, now=now, report=report)

    async def reconcile_startup(self, *, now: datetime) -> DownloadPollReport:
        """启动补扫（A4/B4）：COMPLETED 但 episode 仍 DOWNLOADING 的悬挂任务。

        ``now`` 与 ``poll_once`` 对齐（事件审计用），补扫本身不判时限。
        """
        report = DownloadPollReport()
        completed = await self._store.list_releases_by_status([ReleaseStatus.COMPLETED])
        for release in completed:
            episode_id = release.episode_id
            if episode_id is None:
                continue
            episode = await self._store.get_episode(episode_id)
            if episode is None or self._episode_state(episode) is not EpisodeState.DOWNLOADING:
                continue
            await self._store.transition_episode(episode_id, EpisodeState.DOWNLOADED)
            report.reconciled += 1
            await self._publish(
                EventCategory.DOWNLOAD,
                "download.completed",
                {
                    "torrent_hash": release.torrent_hash,
                    "episode_id": episode_id,
                    "reconciled": True,
                },
            )
        # qB 侧已完成但库内不在途的哈希：如实记 note（不碰非本项目的任务）。
        in_flight = await self._store.list_releases_by_status(
            [ReleaseStatus.PICKED, ReleaseStatus.DOWNLOADING]
        )
        tracked = {release.torrent_hash for release in in_flight}
        try:
            hashes = await self._gateway_completed_hashes()
        except GatewayError:
            return report
        for torrent_hash in hashes:
            if torrent_hash in tracked:
                continue
            report.notes = (
                *report.notes,
                f"untracked completed torrent in downloader: {torrent_hash}",
            )
        return report

    # ------------------------------------------------------------------ misc

    @staticmethod
    def _release_status(release: ReleaseRecord) -> ReleaseStatus:
        return ReleaseStatus(
            release.status.value if hasattr(release.status, "value") else release.status
        )

    @staticmethod
    def _episode_state(episode: Any) -> EpisodeState:
        raw = episode.state
        return EpisodeState(raw.value if hasattr(raw, "value") else raw)

    async def _gateway_status(self, torrent_hash: str) -> dict[str, object] | None:
        return await self._gateway.status(torrent_hash)

    async def _gateway_files(self, torrent_hash: str) -> list[dict[str, object]]:
        try:
            return await self._gateway.files(torrent_hash)
        except GatewayError:
            return []

    async def _gateway_completed_hashes(self) -> list[str]:
        return await self._gateway.completed_hashes()

    async def _publish(self, category: EventCategory, message: str, payload: dict[str, object]) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(Event(category=category, message=message, payload=payload))
        except Exception:  # noqa: BLE001 — 事件/通知永不致命
            logger.warning("event publish failed", exc_info=True)
