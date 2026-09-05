"""媒体库对账（E4b，审核 B5）：ORGANIZED 文件存在性 + 待确认积压告警。

v1 最小版（B5 契约）：启动时对 ORGANIZED 且有 file_path 的集做 stat——
文件不在盘上 → 标 FLAGGED + 通知，**不自动修**；文件恢复（人工处理/
回滚后）可回到 ORGANIZED。反向问题（盘上文件不在库里）v1 不扫（进
backlog）。另按 ``settings.pending_backlog_alert_threshold`` 检查待确认
积压，超阈值发 ``pending.backlog`` 通知（D3 事件之一）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from autoanime.config import Settings
from autoanime.core.enums import EpisodeState
from autoanime.core.events import Event, EventBus, EventCategory
from autoanime.scheduler.store import LoopStore, TransitionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconcileReport:
    """一次对账汇总（B5）。"""

    checked: int
    flagged: int
    pending_backlog: int
    backlog_alert: bool


class LibraryReconciler:
    """启动对账器（只读文件系统 + 受守卫的状态转移，不自动修）。"""

    def __init__(self, store: LoopStore, settings: Settings, *, bus: EventBus | None = None) -> None:
        self._store = store
        self._settings = settings
        self._bus = bus

    async def reconcile(self, *, now: datetime) -> ReconcileReport:
        episodes = await self._store.organized_episodes()
        flagged = 0
        for episode in episodes:
            file_path = episode.file_path
            if not file_path:
                continue
            if Path(file_path).exists():
                continue
            try:
                await self._store.transition_episode(episode.id, EpisodeState.FLAGGED)
            except TransitionError:
                logger.info("episode %s cannot be flagged; skip", episode.id)
                continue
            flagged += 1
            await self._publish(
                EventCategory.NOTIFY,
                "episode.flagged",
                {"episode_id": episode.id, "file_path": file_path},
            )
        backlog = await self._store.count_pending()
        alert = backlog > self._settings.pending_backlog_alert_threshold
        if alert:
            await self._publish(
                EventCategory.NOTIFY,
                "pending.backlog",
                {"open": backlog, "threshold": self._settings.pending_backlog_alert_threshold},
            )
        return ReconcileReport(
            checked=len(episodes), flagged=flagged, pending_backlog=backlog,
            backlog_alert=alert,
        )

    async def _publish(self, category: EventCategory, message: str, payload: dict[str, object]) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(Event(category=category, message=message, payload=payload))
        except Exception:  # noqa: BLE001 — 通知永不致命
            logger.warning("event publish failed", exc_info=True)
