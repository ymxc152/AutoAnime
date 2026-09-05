"""订阅调度运行时（E4，D16 单 backend 进程模型）。

- ``SubscriptionScheduler``：AsyncIOScheduler（APScheduler 3.x 线，4.0 重写
  线勿用）的进程内单例包装——实例由装配方（lifespan / CLI）持有并挂
  ``app.state``，**不引入模块级可变全局状态**（铁律 1）；任务体全部委托
  ``RssPoller`` / ``DownloadPoller``（状态进库，重启恢复，ARCHITECTURE §2）。
- ``build_loop``（``LoopComponents``）：按 Settings 装配 storage + 事件总线
  + 识别管线 + 网关 + 轮询器；CLI ``rerun`` 与调度任务共用同一批入口
  （A7：并发幂等由 store 收口 + torrent_hash 唯一约束 + 状态机守卫兜底）。
- 抖动：APScheduler ``IntervalTrigger(jitter=…)``，幅度 =
  ``rss_poll_jitter_pct``%（默认 ±10%，防整点齐射打源站）。

B6：AsyncIOScheduler 在事件循环内跑；feedparser / qbittorrent-api 等同步
库已在 gateway 与轮询器内部 to_thread 包裹。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from autoanime.config import Settings
from autoanime.core.events import EventBus, InMemoryEventBus
from autoanime.core.interfaces import LlmTransport, Registry
from autoanime.gateway import DownloadGateway, GatewayError, QbittorrentGateway
from autoanime.gateway.rss import RssFetchError, fetch_torrent
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.lookup import StorageMemoryStore
from autoanime.memory.store import SqliteStorage, StorageLlmCacheStore
from autoanime.organize.archive import ArchiveService
from autoanime.pipeline.l3 import ReferenceChain
from autoanime.pipeline.l3_llm import LlmFallbackRecognizer
from autoanime.pipeline.orchestrator import Orchestrator
from autoanime.providers import (
    LLM_TRANSPORT_NAME,
    register_notify,
    register_providers,
    register_reference_providers,
)
from autoanime.scheduler.clock import SystemClock
from autoanime.scheduler.download_poller import CompletedCallback, DownloadPoller
from autoanime.scheduler.library_reconcile import LibraryReconciler
from autoanime.scheduler.rss_poller import RssPoller
from autoanime.scheduler.store import LoopStore

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def build_downloader(settings: Settings) -> DownloadGateway:
    """按 ``settings.downloader`` 装配网关（aria2 只接口 + fake 测试，D5）。"""
    if settings.downloader == "aria2":
        from autoanime.gateway.aria2 import Aria2Gateway

        return Aria2Gateway(
            settings.aria2_rpc_url,
            settings.aria2_secret,
            timeout_s=settings.qbittorrent_timeout_s,
        )
    return QbittorrentGateway(
        settings.qbittorrent_host,
        settings.qbittorrent_port,
        settings.qbittorrent_username,
        settings.qbittorrent_password,
        category=settings.qbittorrent_category,
        timeout_s=settings.qbittorrent_timeout_s,
    )


def build_orchestrator_for_loop(
    settings: Settings, storage: SqliteStorage, registry: Registry | None = None
) -> Orchestrator:
    """与 CLI parse / web lifespan 同一装配方式（记忆飞轮 + 参考源 + L3）。"""
    registry = registry if registry is not None else Registry()
    registered = register_providers(registry, settings)
    transport: LlmTransport | None = None
    if registered:
        transport_obj = registry.optional(LlmTransport, LLM_TRANSPORT_NAME)
        transport = transport_obj if isinstance(transport_obj, LlmTransport) else None
    register_reference_providers(
        registry, cache_store=storage, reference_qps=settings.reference_qps
    )
    reference_chain = ReferenceChain(
        registry, order=settings.reference_order, enabled=settings.reference_enabled
    )
    governance = MemoryGovernance(storage)
    return Orchestrator(
        memory_store=StorageMemoryStore(storage, audit_governance=governance),
        l2_enabled=settings.l2_enabled,
        l3_enabled=settings.llm_enabled,
        l3_recognizer=LlmFallbackRecognizer.from_settings(settings),
        llm_transport=transport,
        llm_cache_store=StorageLlmCacheStore(storage),
        reference_chain=reference_chain,
        audit_sink=governance,
    )


async def refetch_torrent_bytes(
    client_factory: Callable[[], httpx.AsyncClient], source_url: str
) -> bytes | None:
    """失败重试的取种通道（source_url → .torrent 字节）；失败返回 None。"""
    try:
        async with client_factory() as client:
            return await fetch_torrent(client, source_url)
    except (RssFetchError, GatewayError):
        return None


class LoopComponents:
    """订阅闭环的全部组件（CLI rerun 与 lifespan 装配共用）。"""

    def __init__(
        self,
        *,
        store: LoopStore,
        storage: SqliteStorage,
        orchestrator: Orchestrator,
        gateway: DownloadGateway,
        rss_poller: RssPoller,
        download_poller: DownloadPoller,
        bus: EventBus,
        archive_service: ArchiveService | None = None,
        reconciler: LibraryReconciler | None = None,
        notify_dispatcher: Any | None = None,
        own_storage: bool = False,
    ) -> None:
        self.store = store
        self.storage = storage
        self.orchestrator = orchestrator
        self.gateway = gateway
        self.rss_poller = rss_poller
        self.download_poller = download_poller
        self.bus = bus
        self.archive_service = archive_service
        self.reconciler = reconciler
        self.notify_dispatcher = notify_dispatcher
        self.own_storage = own_storage

    async def close(self) -> None:
        if self.own_storage:
            await self.storage.close()


def build_loop(
    settings: Settings,
    *,
    storage: SqliteStorage | None = None,
    bus: EventBus | None = None,
    on_completed: CompletedCallback | None = None,
) -> LoopComponents:
    """按 Settings 装配闭环组件（不启动调度线程；任务由手动/计划触发）。"""
    own_storage = storage is None
    storage = storage if storage is not None else SqliteStorage(settings.database_url)
    bus = bus if bus is not None else InMemoryEventBus()
    store = LoopStore(storage)
    orchestrator = build_orchestrator_for_loop(settings, storage)
    gateway = build_downloader(settings)
    timeout = settings.rss_fetch_timeout_s

    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout)

    async def _refetch(source_url: str) -> bytes | None:
        return await refetch_torrent_bytes(client_factory, source_url)

    governance = MemoryGovernance(storage)
    archive_service = ArchiveService(
        store, orchestrator, gateway,
        settings=settings, governance=governance, bus=bus,
    )
    reconciler = LibraryReconciler(store, settings, bus=bus)
    registry = Registry()
    notify_dispatcher = register_notify(registry, settings)

    rss_poller = RssPoller(
        store,
        orchestrator,
        gateway,
        bus=bus,
        fetch_retries=settings.rss_fetch_retries,
        fetch_timeout_s=settings.rss_fetch_timeout_s,
        upgrade_threshold=settings.upgrade_threshold,
        upgrade_max_per_episode=settings.upgrade_max_per_episode,
        client_factory=client_factory,
        rss_token=None,  # per-source token 在 rss_sources 行内；此为兜底位
    )
    download_poller = DownloadPoller(
        store,
        gateway,
        bus=bus,
        max_retries=settings.download_max_retries,
        on_completed=on_completed if on_completed is not None else archive_service.handle_completed,
        torrent_refetch=_refetch,
    )
    return LoopComponents(
        store=store,
        storage=storage,
        orchestrator=orchestrator,
        gateway=gateway,
        rss_poller=rss_poller,
        download_poller=download_poller,
        bus=bus,
        archive_service=archive_service,
        reconciler=reconciler,
        notify_dispatcher=notify_dispatcher,
        own_storage=own_storage,
    )


class SubscriptionScheduler:
    """AsyncIOScheduler 的进程内单例包装（D16：与 FastAPI 同进程生命周期）。

    ``start`` 幂等（重复调用忽略）；``shutdown`` 幂等。任务体是「一轮
    poll」，异常吞掉记日志（调度任务永不向上抛、不崩进程）。
    """

    def __init__(self, components: LoopComponents, settings: Settings) -> None:
        self._components = components
        self._settings = settings
        self._scheduler: AsyncIOScheduler | None = None
        self._pump_task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def start(self) -> None:
        if self.running:
            logger.warning("subscription scheduler already running; ignore start()")
            return
        scheduler = AsyncIOScheduler()
        jitter_seconds = max(
            1,
            int(
                self._settings.rss_poll_interval_minutes
                * 60
                * self._settings.rss_poll_jitter_pct
                / 100
            ),
        )
        # RSS 轮询：默认 30min ± 10% 抖动（整点齐射对策）；COLLECTED 降频
        # 判定在轮询器内按季状态收口（cadence.should_poll_season）。
        scheduler.add_job(
            self._run_rss_poll,
            "interval",
            minutes=self._settings.rss_poll_interval_minutes,
            jitter=jitter_seconds,
            id="rss_poll",
            max_instances=1,
            coalesce=True,
        )
        # 下载进度轮询：qB 无 webhook（A4），短间隔比对 state。
        scheduler.add_job(
            self._run_download_poll,
            "interval",
            seconds=self._settings.download_poll_interval_s,
            id="download_poll",
            max_instances=1,
            coalesce=True,
        )
        # COLLECTED 月度降频的显式兜底任务（轮询器内同样有 30 天闸，双保险）。
        scheduler.add_job(
            self._run_rss_poll,
            "interval",
            days=self._settings.collected_check_days,
            id="collected_check",
            max_instances=1,
            coalesce=True,
        )
        self._scheduler = scheduler
        scheduler.start()
        self._start_notify_pump()
        logger.info(
            "subscription scheduler started (rss=%smin jitter±%ss, download=%ss)",
            self._settings.rss_poll_interval_minutes,
            jitter_seconds,
            self._settings.download_poll_interval_s,
        )

    def shutdown(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            self._pump_task = None
        if self._scheduler is None:
            return
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._scheduler = None
        logger.info("subscription scheduler stopped")

    async def run_startup_cycle(self) -> None:
        """启动补扫（A4/B4/B5）：下载悬挂对账 + 媒体库对账 + 首轮下载比对。"""
        now = SystemClock().now()
        if self._components.reconciler is not None:
            try:
                report = await self._components.reconciler.reconcile(now=now)
                if report.flagged:
                    logger.warning("library reconcile: %s episodes flagged", report.flagged)
            except Exception:  # noqa: BLE001 — 对账失败不阻塞启动
                logger.exception("library reconcile failed")
        try:
            reconcile = await self._components.download_poller.reconcile_startup(now=now)
            logger.info("startup reconcile: %s reconciled", reconcile.reconciled)
        except GatewayError as exc:
            logger.warning("startup reconcile skipped (downloader unreachable): %s", exc)
        try:
            await self._components.download_poller.poll_once(now=now)
        except GatewayError as exc:
            logger.warning("first download poll skipped: %s", exc)

    def _start_notify_pump(self) -> None:
        """通知泵（D3/D16）：进程内总线 → NotifyDispatcher 白名单扇出。

        仅当总线提供 ``subscribe()``（InMemoryEventBus）且配置了通知通道
        时启动；CLI 短生命周期路径自动跳过。
        """
        dispatcher = self._components.notify_dispatcher
        subscribe = getattr(self._components.bus, "subscribe", None)
        if dispatcher is None or not callable(subscribe):
            return
        subscription: Any = subscribe()

        async def _pump() -> None:
            while True:
                event: Any = await subscription.queue.get()
                if event is None:
                    return
                await dispatcher.dispatch(event)

        self._pump_task = asyncio.create_task(_pump(), name="autoanime-notify-pump")
        logger.info("notify pump started (%s)", sorted(dispatcher.subscribed_events))

    async def _run_rss_poll(self) -> None:
        try:
            report = await self._components.rss_poller.poll_all(now=SystemClock().now())
            logger.info(
                "rss poll: picked=%s gaps=%s errors=%s",
                report.picked,
                report.all_gaps,
                report.errors or "none",
            )
        except Exception:  # noqa: BLE001 — 调度任务永不向上抛
            logger.exception("rss poll job failed")

    async def _run_download_poll(self) -> None:
        try:
            report = await self._components.download_poller.poll_once(
                now=SystemClock().now()
            )
            logger.info(
                "download poll: checked=%s completed=%s failed=%s retried=%s",
                report.checked, report.completed, report.failed, report.retried,
            )
        except Exception:  # noqa: BLE001
            logger.exception("download poll job failed")


__all__ = [
    "LoopComponents",
    "SubscriptionScheduler",
    "build_downloader",
    "build_loop",
    "build_orchestrator_for_loop",
    "refetch_torrent_bytes",
]
