"""库外自动入库（qB 自带 RSS 下载器等外部下载源 → 媒体库）。

场景：用户在 qBittorrent 里自配 RSS 订阅自动下载，文件不经本系统的
RSS 轮询管线落 ReleaseRecord/episode 状态——此前只能靠「导入与识别」页
手动导入。本扫描器定时对 ``download_path`` 跑同一套识别归档链，护栏是
**只收命中已有订阅的文件**（通用 PT feed 里的无关番不建条目、不动文件、
不进待确认队列）。

流程：扫描视频文件 → 跳过已入库/已排队（文件名级，与手动导入同规则）→
L1 解析 + align 对齐到已有 (series, season) → 命中子集走与手动导入相同
的收口（``_handle_import_outcome``：archive/pending/organize 归档，尊重
全局 dry_run）→ 未命中忽略计数。

命中订阅的文件把 series 行随收口下发：归档目录跟随**订阅命名标题**
（按 ``naming_title_language`` 回退链）——与 RSS/归档服务路径同一目录名，
不再按 L1 解析名把同番裂成两个目录（批次一修复）。

副作用边界：dry_run=true 时只匹配计数不触碰文件；命中但收口为 pending
的文件进 pending_queue（与手动导入一致，供人工确认学习）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from autoanime.config import Settings
from autoanime.core.enums import Segment
from autoanime.core.interfaces import RawName
from autoanime.core.models import Season, Series
from autoanime.memory.governance import MemoryGovernance
from autoanime.organize.expected import align_rss_entry
from autoanime.scheduler.store import LoopStore

logger = logging.getLogger(__name__)


@dataclass
class AutoScanReport:
    """一次扫描的小计（SSE/日志用）。"""

    enabled: bool = True
    dry_run: bool = False
    total: int = 0
    fresh: int = 0
    matched: int = 0
    archived: int = 0
    pending: int = 0
    failed: int = 0
    ignored: int = 0
    skipped: int = 0
    error: str | None = None
    matched_files: list[str] = field(default_factory=list)


#: 收口函数签名（默认 cli._handle_import_outcome；测试注入 fake）
IngestBatchFn = Callable[..., Awaitable[dict[Path, str]]]


class LibraryAutoScanner:
    """库外下载目录扫描器：只收命中已有订阅的文件,其余忽略。"""

    def __init__(
        self,
        store: LoopStore,
        storage: Any,
        settings: Settings,
        *,
        bus: Any = None,
        scan_root: Path | None = None,
        l1: Any = None,
        ingest_batch: IngestBatchFn | None = None,
        scan_fn: Callable[[Path], tuple[int, list[Path]]] | None = None,
    ) -> None:
        self._store = store
        self._storage = storage
        self._settings = settings
        self._bus = bus
        self._scan_root = scan_root
        self._l1 = l1
        self._ingest_batch = ingest_batch
        self._scan_fn = scan_fn

    # ------------------------------------------------------------------ run

    async def scan_and_ingest(self, *, now: datetime) -> AutoScanReport:
        report = AutoScanReport(enabled=self._settings.library_autoscan_enabled)
        if not report.enabled:
            return report
        report.dry_run = self._settings.dry_run
        root = self._scan_root or Path(self._settings.download_path)
        if not root.is_dir():
            report.error = f"download dir missing: {root}"
            return report

        try:
            scan = self._scan_fn or _default_scan
            total, videos = scan(root)
            report.total = total
            fresh = await self._fresh_videos(videos, report)
            report.fresh = len(fresh)
            if not fresh:
                return report

            # 订阅上下文:series 标题集 + 季号列表（对齐目标）
            contexts = await self._subscription_contexts()
            l1 = self._l1 or self._default_l1()
            matched, ignored = await self._match(fresh, contexts, l1)
            report.matched = len(matched)
            report.ignored = ignored
            report.matched_files = [str(f) for f, _series, _sn in matched]
            if not matched:
                return report

            if report.dry_run:
                # 只计划:计数不动文件（与全局试运行语义一致）
                return report

            archived, pending, failed = await self._ingest(matched)
            report.archived = archived
            report.pending = pending
            report.failed = failed
            await self._publish(
                "library.autoscan",
                {
                    "total": report.total,
                    "fresh": report.fresh,
                    "matched": report.matched,
                    "archived": report.archived,
                    "pending": report.pending,
                    "ignored": report.ignored,
                },
            )
        except Exception as exc:  # noqa: BLE001 — 扫描失败不影响其他调度任务
            logger.exception("library autoscan failed")
            report.error = f"{type(exc).__name__}: {exc}"
        return report

    # -------------------------------------------------------------- steps

    async def _fresh_videos(self, videos: list[Path], report: AutoScanReport) -> list[Path]:
        """排除已入库/已排队（文件名级,与手动导入同规则）。"""
        handled: dict[str, str] = {}
        for name in await self._store.archived_file_names():
            handled[name] = "already-archived"
        for name in await self._store.open_pending_raw_names():
            handled.setdefault(name, "already-pending")
        fresh: list[Path] = []
        for file in videos:
            reason = handled.get(file.name)
            if reason is None:
                fresh.append(file)
            else:
                report.skipped += 1
        return fresh

    async def _subscription_contexts(
        self,
    ) -> list[tuple[set[str], tuple[int, ...], Series]]:
        """全部订阅的 (标题集合, 季号列表, series 行)——对齐目标。

        series 行随命中结果一路传给收口（``_handle_import_outcome`` 的
        series 上下文）：归档命名标题与库收纳 titles 改用订阅记录的标题，
        与 RSS/归档服务路径一致，同番不再按 L1 解析名裂成第二个目录。
        """
        async with self._storage.transaction() as session:
            series_rows = (
                (await session.execute(sa.select(Series))).scalars().all()
            )
            season_rows = (await session.execute(sa.select(Season))).scalars().all()
        seasons_by_series: dict[int, list[int]] = {}
        for row in season_rows:
            seasons_by_series.setdefault(row.series_id, []).append(row.number)
        contexts: list[tuple[set[str], tuple[int, ...], Series]] = []
        for row in series_rows:
            titles = {
                t
                for t in (row.title_cn, row.title_jp, row.title_romaji)
                if t is not None and t.strip() != ""
            }
            if not titles:
                continue
            contexts.append((titles, tuple(sorted(seasons_by_series.get(row.id, []))), row))
        return contexts

    async def _match(
        self,
        fresh: list[Path],
        contexts: list[tuple[set[str], tuple[int, ...], Series]],
        l1: Any,
    ) -> tuple[list[tuple[Path, Series, int]], int]:
        """L1 解析 + align:命中已有订阅(同番同季带集号)的文件入选。"""
        matched: list[tuple[Path, Series, int]] = []
        ignored = 0
        for file in fresh:
            parse = await l1.parse(RawName(name=file.name, folder=file.parent.name))
            hit: tuple[Path, Series, int] | None = None
            if parse is not None and parse.segment is Segment.EPISODE:
                for titles, season_numbers, series in contexts:
                    for season_number in season_numbers or (1,):
                        alignment = align_rss_entry(
                            parse, expected_titles=tuple(sorted(titles)), season_number=season_number
                        )
                        if alignment.verdict == "fast_path":
                            hit = (file, series, season_number)
                            break
                    if hit is not None:
                        break
            if hit is None:
                ignored += 1
            else:
                matched.append(hit)
        return matched, ignored

    async def _ingest(
        self, matched: list[tuple[Path, Series, int]]
    ) -> tuple[int, int, int]:
        """命中的子集走与手动导入相同的收口（archive/pending/失败计数）。

        文件 → 命中订阅行的映射随收口下发：归档目录跟随订阅命名标题。
        """

        ingest = self._ingest_batch or _default_ingest_batch
        storage, governance = await self._ingest_dependencies()
        series_by_file = {file: series for file, series, _sn in matched}
        files = list(series_by_file)
        actions = await ingest(
            files,
            settings=self._settings,
            store=self._store,
            governance=governance,
            storage=storage,
            dry_run=False,
            series_by_file=series_by_file,
        )
        archived = pending = failed = 0
        for file in files:
            action = actions.get(file, "failed")
            if action == "archive":
                archived += 1
            elif action == "pending":
                pending += 1
            else:
                failed += 1
        return archived, pending, failed

    async def _ingest_dependencies(self) -> tuple[Any, MemoryGovernance]:
        """收口依赖:默认给真实 storage/governance（测试注入可覆盖 storage）。"""
        return self._storage, MemoryGovernance(self._storage)

    def _default_l1(self) -> Any:
        from autoanime.pipeline.l1_local import LocalRecognizer

        return LocalRecognizer()

    async def _publish(self, message: str, payload: dict[str, object]) -> None:
        if self._bus is None:
            return
        try:
            from autoanime.core.events import Event, EventCategory

            await self._bus.publish(Event(EventCategory.DOWNLOAD, message, payload))
        except Exception:  # noqa: BLE001 — 通知永不致命
            logger.warning("autoscan event publish failed", exc_info=True)


def _default_scan(root: Path) -> tuple[int, list[Path]]:
    from autoanime.cli import _scan_video_files

    return _scan_video_files(root)


async def _default_ingest_batch(
    files: list[Path],
    *,
    settings: Settings,
    store: LoopStore,
    governance: MemoryGovernance,
    storage: Any,
    dry_run: bool,
    series_by_file: dict[Path, Any] | None = None,
) -> dict[Path, str]:
    """命中文件的全管线收口（与手动导入同一处理函数）。

    ``series_by_file``：文件 → 命中订阅行（``_ingest`` 下发）。收口据此把
    归档命名标题/库收纳 titles 切到订阅记录标题（与 RSS/归档服务路径一致）。
    """
    from autoanime.cli import _build_orchestrator, _group_by_parent, _handle_import_outcome
    from autoanime.core.interfaces import RawName

    orchestrator, orchestrator_storage, transport = await _build_orchestrator(
        settings, metrics=True, dry_run=dry_run
    )
    bindings = series_by_file or {}
    actions: dict[Path, str] = {}
    try:
        for parent, group in _group_by_parent(files):
            raws = [
                RawName(name=f.name, folder=parent.name, parent_path=str(parent))
                for f in group
            ]
            outcomes = await orchestrator.process_batch(
                raws,
                batching=True,
                batch_min_size=settings.batch_min_size,
                batch_max_size=settings.batch_max_size,
            )
            for file, outcome in zip(group, outcomes, strict=True):
                item = await _handle_import_outcome(
                    file,
                    outcome,
                    settings=settings,
                    store=store,
                    governance=governance,
                    dry_run=dry_run,
                    series=bindings.get(file),
                )
                actions[file] = str(item.get("action", "failed"))
    finally:
        aclose = getattr(transport, "aclose", None)
        if aclose is not None:
            await aclose()
    return actions
