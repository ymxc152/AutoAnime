"""PosterService 单元测试（PR3+）：负缓存冷却/pending 并发护栏/调度函数。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from autoanime.config import Settings
from autoanime.core.interfaces import MetadataReference, Registry
from autoanime.core.models import PosterFetch
from autoanime.memory.store import SqliteStorage
from autoanime.organize.poster import (
    PENDING_STALENESS_S,
    PosterService,
    poster_folders,
    schedule_poster_fetch,
)
from autoanime.pipeline.l3 import ReferenceChain
from autoanime.pipeline.l3.reference import ReferenceFacts


class _FakeDownloader:
    """可编程下载 fake：记录调用次数。"""

    def __init__(self, result: tuple[bytes, str] | None) -> None:
        self._result = result
        self.calls = 0

    async def fetch(self, url: str) -> tuple[bytes, str] | None:
        self.calls += 1
        return self._result


def _chain_with_facts(facts: ReferenceFacts | None) -> ReferenceChain:
    """恒定返回同一 facts 的真 ReferenceChain（内挂假 provider，零外呼）。"""

    class _StaticProvider:
        async def lookup(self, title_shape: str) -> ReferenceFacts | None:
            return facts

    registry = Registry()
    registry.register(MetadataReference, "bangumi")(_StaticProvider())
    return ReferenceChain(registry, order=("bangumi",), enabled=True)


def _service(
    storage: SqliteStorage,
    settings: Settings,
    downloader: _FakeDownloader,
    chain: ReferenceChain | None,
) -> PosterService:
    return PosterService(
        storage=storage,
        settings=settings,
        chain_provider=lambda: chain,
        downloader=downloader,
    )


def test_poster_folders_dedupes_and_skips_unknown() -> None:
    assert poster_folders(("葬送的芙莉莲", None, "葬送的芙莉莲")) == ["葬送的芙莉莲"]
    assert poster_folders((None, "Sousou no Frieren", "葬送のフリーレン")) == [
        "Sousou no Frieren",
        "葬送のフリーレン",
    ]
    # 非法字符清洗 + 纯符号 → Unknown 跳过
    assert poster_folders(("剧场版: 声之形",)) == ["剧场版 声之形"]
    assert poster_folders((":?:",)) == []


async def test_pending_guard_blocks_concurrent_and_staleness_allows_retry(
    tmp_path: Path,
) -> None:
    """新鲜 pending 并发护栏挡重复外呼；僵死 pending（超窗口）允许重试。"""
    settings = Settings()
    settings.poster_retry_cooldown_days = 7
    async with SqliteStorage("sqlite+aiosqlite:///:memory:") as storage:
        await storage.create_all()
        downloader = _FakeDownloader((b"jpeg", "image/jpeg"))
        service = _service(
            storage,
            settings,
            downloader,
            _chain_with_facts(
                ReferenceFacts(poster_url="https://img.example/p.jpg", source="bangumi")
            ),
        )
        await storage.upsert_poster_fetch(
            PosterFetch(folder="Some Show", status="pending", fetched_at=datetime.now())
        )
        titles: tuple[str | None, ...] = ("Some Show", None, None)
        assert await service.ensure_poster(titles=titles, library_path=tmp_path) is None
        assert downloader.calls == 0

        # 僵死 pending（超过 staleness 窗口）→ 允许重试并落盘
        stale = await storage.find_poster_fetch("Some Show")
        assert stale is not None
        stale.fetched_at = datetime.now() - timedelta(seconds=PENDING_STALENESS_S + 1)
        await storage.upsert_poster_fetch(stale)
        ext = await service.ensure_poster(titles=titles, library_path=tmp_path)
        assert ext == ".jpg"
        assert downloader.calls == 1
        assert (tmp_path / "Some Show" / "poster.jpg").is_file()
        row = await storage.find_poster_fetch("Some Show")
        assert row is not None and row.status == "fetched" and row.ext == ".jpg"
        await service.aclose()


async def test_fetched_status_with_missing_file_retries(tmp_path: Path) -> None:
    """fetched 但本地文件被删 → 允许重拉（不信任状态记录）。"""
    settings = Settings()
    async with SqliteStorage("sqlite+aiosqlite:///:memory:") as storage:
        await storage.create_all()
        downloader = _FakeDownloader((b"png", "image/png"))
        service = _service(
            storage,
            settings,
            downloader,
            _chain_with_facts(
                ReferenceFacts(poster_url="https://img.example/p.png", source="bangumi")
            ),
        )
        await storage.upsert_poster_fetch(
            PosterFetch(folder="Deleted", status="fetched", ext=".jpg", fetched_at=datetime.now())
        )
        ext = await service.ensure_poster(titles=("Deleted",), library_path=tmp_path)
        assert ext == ".png"
        assert downloader.calls == 1
        await service.aclose()


async def test_schedule_poster_fetch_skips_none_and_disabled(tmp_path: Path) -> None:
    """调度函数：服务未装配/开关关闭时静默跳过，不创建任务。"""
    settings = Settings()
    settings.poster_download_enabled = False
    async with SqliteStorage("sqlite+aiosqlite:///:memory:") as storage:
        await storage.create_all()
        downloader = _FakeDownloader(None)
        disabled = _service(storage, settings, downloader, None)
        schedule_poster_fetch(disabled, titles=("X",), library_path=tmp_path)
        schedule_poster_fetch(None, titles=("X",), library_path=tmp_path)
        assert downloader.calls == 0
        await disabled.aclose()


async def test_schedule_poster_fetch_runs_background_task(
    tmp_path: Path,
) -> None:
    """开关开启 → 任务创建并完成落盘。

    Windows 上线程池首次调度 + 临时目录首次 mkdir 可能偶发数百毫秒
    （Defender 实时扫描），轮询等待任务完成，上限 5 秒。
    """
    import autoanime.organize.poster as poster_module

    settings = Settings()
    async with SqliteStorage("sqlite+aiosqlite:///:memory:") as storage:
        await storage.create_all()
        downloader = _FakeDownloader((b"webp", "image/webp"))
        service = _service(
            storage,
            settings,
            downloader,
            _chain_with_facts(
                ReferenceFacts(poster_url="https://img.example/p.webp", source="bangumi")
            ),
        )
        schedule_poster_fetch(service, titles=("Bg Show",), library_path=tmp_path)
        for _ in range(100):
            if all(task.done() for task in poster_module._background_tasks):
                break
            await asyncio.sleep(0.05)
        assert all(task.done() for task in poster_module._background_tasks)
        assert (tmp_path / "Bg Show" / "poster.webp").is_file()
        assert downloader.calls == 1
        row = await storage.find_poster_fetch("Bg Show")
        assert row is not None and row.status == "fetched" and row.ext == ".webp"
        await service.aclose()
