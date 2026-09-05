"""D16 组合 lifespan 单测（E4b）：create_app 工厂同进程承载 API + 调度器。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoanime.config import Settings
from autoanime.scheduler.asgi import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'asgi.db').as_posix()}",
        library_path=tmp_path / "library",
        download_path=tmp_path / "downloads",
        quarantine_path=tmp_path / "quarantine",
        downloader="aria2",
        aria2_rpc_url="http://127.0.0.1:1/jsonrpc",  # 不可达（离线）
        rss_poll_interval_minutes=30,
    )


def test_asgi_factory_serves_api_and_starts_scheduler(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        scheduler = getattr(app.state, "scheduler", None)
        assert scheduler is not None
        assert scheduler.running is True  # D16：API + 调度同进程
        components = getattr(app.state, "loop_components", None)
        assert components is not None
        assert components.archive_service is not None
        assert components.reconciler is not None
    assert scheduler.running is False  # lifespan 退出即停


def test_asgi_scheduler_start_is_idempotent(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app):
        scheduler = app.state.scheduler
        scheduler.start()  # 重复 start 幂等（不炸）
        assert scheduler.running is True
    assert scheduler.running is False
