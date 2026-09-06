"""海报兜底下载服务（PR3+）。

本地库缺失 ``poster.{jpg,jpeg,png,webp}`` 时，从参考源（Bangumi/TMDB）
拉取海报图落盘到 ``{library}/{sanitize(标题)}/poster.{ext}``——与海报端
点的 local-first 候选契约（``web.routers.series._poster_candidates``）
完全兼容：落盘后走本地直读，后续请求零外呼。

两个触发点（均为 best-effort：失败只记日志/负缓存，不 crash、不阻塞
归档主链路、不阻塞 Library 渲染）：

- A. confirm/归档成功后（``schedule_poster_fetch`` 后台任务，搭 alias
  回填链便车）；
- B. 海报端点懒拉取兜底（本地 404 且未处于负缓存冷却期）。

明确不做：添加订阅时不拉取（订阅可能被放弃，浪费 QPS 且留空目录）。

负缓存（必须）：``poster_fetch`` 表按库内目录名记 ``fetched`` /
``missing`` / ``pending``——``missing`` 进入冷却期
（``poster_retry_cooldown_days``，默认 7 天）内懒拉取直接 404 不再外呼；
``pending`` 是下载进行中的并发护栏，超过 staleness 窗口视为僵死允许重试。

网络约束：图片 CDN 直链（lain.bgm.tv / image.tmdb.org）不走参考源 API
配额，但下载复用 ``ReferenceHttpClient`` 的 QPS 节流/超时/429 退避，且
httpx ``trust_env`` 默认读取 ``HTTPS_PROXY``（与 Mikan 同款代理语义）。
URL 仅来自参考源 adapter 响应（非用户输入）；扩展名按响应 Content-Type
白名单映射，白名单外不落盘。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

from autoanime.config import Settings
from autoanime.core.models import PosterFetch
from autoanime.memory.store import SqliteStorage
from autoanime.organize.naming import sanitize
from autoanime.pipeline.l3 import ReferenceChain
from autoanime.providers._reference_http import DEFAULT_QPS, ReferenceHttpClient

logger = logging.getLogger(__name__)

POSTER_CONTENT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
"""响应 Content-Type → 落盘扩展名白名单（白名单外不落盘）。"""

POSTER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
"""本地候选扩展名（与海报端点契约一致，顺序即优先级）。"""

PENDING_STALENESS_S = 60.0
"""``pending`` 状态的僵死判定窗口：超时未收敛视为下载已死，允许重试。"""


def poster_folders(titles: Sequence[str | None]) -> list[str]:
    """标题序列 → 去重后的库内候选目录名（与端点候选推导同源）。

    ``naming.sanitize`` 清洗后为 ``Unknown`` 的标题跳过（该目录名无意义，
    端点同样不会为其构造候选）。
    """
    folders: list[str] = []
    for title in titles:
        if not title:
            continue
        cleaned = sanitize(title)
        if cleaned != "Unknown" and cleaned not in folders:
            folders.append(cleaned)
    return folders


def find_existing_poster_ext(directory: Path) -> str | None:
    """目录内已有 poster 文件则返回其实际扩展名（含点），否则 ``None``。"""
    for ext in POSTER_EXTENSIONS:
        if (directory / f"poster{ext}").is_file():
            return ext
    return None


@runtime_checkable
class PosterDownload(Protocol):
    """海报图片下载窄协议：返回 ``(字节体, Content-Type)``，失败 ``None``。"""

    async def fetch(self, url: str) -> tuple[bytes, str] | None: ...


class ReferencePosterDownloader:
    """经 ``ReferenceHttpClient`` 的海报下载（QPS 节流/超时/429 退避复用）。

    ``transport``/``clock``/``sleeper`` 可注入（离线测试）；真实网络时
    httpx ``trust_env`` 读取 ``HTTPS_PROXY``（图片 CDN 国内可能被墙，与
    Mikan 拉取同款代理语义）。
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        timeout_s: float = 15.0,
        qps: float = DEFAULT_QPS,
    ) -> None:
        self._http = ReferenceHttpClient(
            transport=transport,
            clock=clock,
            sleeper=sleeper,
            timeout_s=timeout_s,
            qps=qps,
        )

    async def fetch(self, url: str) -> tuple[bytes, str] | None:
        return await self._http.request_content("GET", url)

    async def aclose(self) -> None:
        await self._http.aclose()


class PosterService:
    """海报兜底下载编排：本地检查 → 负缓存/并发护栏 → 链查询 → 下载落盘。"""

    def __init__(
        self,
        *,
        storage: SqliteStorage,
        settings: Settings,
        chain_provider: Callable[[], ReferenceChain | None],
        downloader: PosterDownload | None = None,
    ) -> None:
        self._storage = storage
        self._settings = settings
        # chain 经 provider 惰性取（app.state.reference_chain 可被测试整体替换）
        self._chain_provider = chain_provider
        self._downloader: PosterDownload = downloader if downloader is not None else (
            ReferencePosterDownloader(
                timeout_s=settings.poster_download_timeout_s,
                qps=settings.reference_qps if settings.reference_qps and settings.reference_qps > 0 else DEFAULT_QPS,
            )
        )

    @property
    def enabled(self) -> bool:
        return self._settings.poster_download_enabled

    async def aclose(self) -> None:
        downloader = self._downloader
        if isinstance(downloader, ReferencePosterDownloader):
            await downloader.aclose()

    async def ensure_poster(self, *, titles: Sequence[str | None], library_path: Path) -> str | None:
        """确保标题对应目录有 poster 文件；返回落盘/已有扩展名，失败 ``None``。

        全程 best-effort：任何失败路径返回 ``None``（端点据此保持 404，
        前端占位降级），绝不抛异常。
        """
        folders = poster_folders(titles)
        if not folders:
            return None
        folder = folders[0]
        directory = Path(library_path) / folder
        existing = find_existing_poster_ext(directory)
        if existing is not None:
            return existing
        if not self._settings.poster_download_enabled:
            return None
        try:
            return await self._fetch_and_store(folder=folder, titles=titles, library_path=library_path)
        except Exception:  # noqa: BLE001 -- 兜底：任何意外都不进调用方（best-effort 契约）
            logger.warning("poster fetch failed: folder=%s", folder, exc_info=True)
            try:
                await self._mark(folder, status="missing", url=None)
            except Exception:  # noqa: BLE001 -- 负缓存写失败同样不外抛
                logger.warning("poster negative cache write failed: folder=%s", folder)
            return None

    async def _fetch_and_store(self, *, folder: str, titles: Sequence[str | None], library_path: Path) -> str | None:
        now = datetime.now()
        row = await self._storage.find_poster_fetch(folder)
        if row is not None and row.status == "missing":
            cooldown = timedelta(days=self._settings.poster_retry_cooldown_days)
            if now - row.fetched_at < cooldown:
                return None
        if row is not None and row.status == "pending":
            if (now - row.fetched_at).total_seconds() < PENDING_STALENESS_S:
                # 下载进行中：本次直接放弃（浏览器侧占位降级，落盘后刷新可见）
                return None
        # status == "fetched" 但本地文件已丢失（被手动删除）：允许重拉，
        # 落到下方正常流程。
        chain = self._chain_provider()
        if chain is None:
            return None
        query = next((title for title in titles if title), None)
        if not query:
            return None
        # 并发护栏先占位：并发请求看到新鲜 pending 即放弃，不重复外呼。
        await self._mark(folder, status="pending", url=row.url if row is not None else None)
        facts = await chain.lookup(query)
        url = facts.poster_url if facts is not None else None
        if not url:
            await self._mark(folder, status="missing", url=None)
            return None
        download = await self._downloader.fetch(url)
        if download is None:
            await self._mark(folder, status="missing", url=url)
            return None
        body, content_type = download
        ext = POSTER_CONTENT_TYPES.get(content_type)
        if ext is None or not body:
            await self._mark(folder, status="missing", url=url)
            return None
        target = Path(library_path) / folder / f"poster{ext}"
        await asyncio.to_thread(_write_bytes, target, body)
        await self._mark(folder, status="fetched", ext=ext, url=url)
        logger.info("poster fetched: folder=%s ext=%s url=%s", folder, ext, url)
        return ext

    async def _mark(self, folder: str, *, status: str, url: str | None = None, ext: str | None = None) -> None:
        await self._storage.upsert_poster_fetch(
            PosterFetch(folder=folder, status=status, ext=ext, url=url, fetched_at=datetime.now())
        )


def _write_bytes(target: Path, body: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)


_background_tasks: set[asyncio.Task[object]] = set()
"""在途后台任务的强引用集（事件循环只持弱引用，不持即可能被 GC 静默丢弃）。"""


def schedule_poster_fetch(
    service: PosterService | None,
    *,
    titles: Sequence[str | None],
    library_path: Path,
) -> None:
    """归档成功后的 best-effort 海报拉取（触发点 A，后台任务）。

    服务未装配/已关闭/冷却期内时静默跳过；任务异常只打日志（ensure_poster
    内部已兜底，这里是双保险），绝不影响归档结果与调用方。任务经模块级
    集合持强引用（防 GC），完成即回收。
    """
    if service is None or not service.enabled:
        return
    task = asyncio.create_task(service.ensure_poster(titles=titles, library_path=library_path))
    _background_tasks.add(task)
    task.add_done_callback(_log_task_exception)


def _log_task_exception(task: asyncio.Task[object]) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("background poster fetch task failed: %s", exc)
