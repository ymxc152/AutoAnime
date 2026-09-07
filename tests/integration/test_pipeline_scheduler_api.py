from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from autoanime.config import Settings
from autoanime.web.app import create_app


@pytest.fixture
async def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    import os

    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    value = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'pipeline.db').as_posix()}",
        library_path=tmp_path / "library",
        download_path=tmp_path / "downloads",
        quarantine_path=tmp_path / "quarantine",
        downloader="aria2",
        aria2_rpc_url="http://127.0.0.1:1/jsonrpc",
        reference_enabled=False,
        api_sse_heartbeat_s=0.2,
    )
    value.download_path.mkdir(parents=True, exist_ok=True)
    return value


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_parse_preview_is_offline_and_does_not_write(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/pipeline/parse-preview",
        json={"name": "Show S01E01 1080p.mkv"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["route"] == "archive"
    assert body["result"]["title"] == "Show"
    assert body["result"]["episode"] == 1


async def test_pipeline_import_task_reports_dry_run_without_moving_files(
    client: httpx.AsyncClient, settings: Settings, tmp_path: Path
) -> None:
    source = settings.download_path / "Show S01E01 1080p.mkv"
    source.write_bytes(b"0" * 32)
    resp = await client.post(
        "/api/pipeline/import",
        json={"directory": str(settings.download_path), "dry_run": True},
    )
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]
    task = (await client.get(f"/api/pipeline/tasks/{task_id}")).json()
    assert task["status"] == "completed", task
    assert task["summary"]["scanned"] == 1
    assert task["summary"]["archived"] == 1
    assert source.exists()
    assert not list(settings.library_path.rglob("*.mkv"))


async def test_scheduler_run_once_offline_reports_degraded_sources(client) -> None:
    resp = await client.post("/api/scheduler/run-once", json={"scope": "all"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "all"
    assert body["reports"]["rss"]["picked"] == 0
    assert body["reports"]["download"]["checked"] == 0




