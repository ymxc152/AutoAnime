"""认证中间件集成测试（D6/B7）：ASGI 传输 + 非流式请求（SSE 冒烟走真实 uvicorn）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from autoanime.config import Settings
from autoanime.web.app import create_app


@pytest.fixture
async def token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)


def _settings(tmp_path: Path, token: str) -> Settings:
    instance = Settings()
    instance.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'auth.db').as_posix()}"
    instance.reference_enabled = False
    from pydantic import SecretStr

    instance.api_token = SecretStr(token)
    return instance


@pytest.fixture
async def client(tmp_path: Path, token_env) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(_settings(tmp_path, "t0ken"))
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_missing_token_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/health")
    assert resp.status_code == 401


async def test_wrong_token_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/health", headers={"X-API-Token": "wrong"})
    assert resp.status_code == 401


async def test_header_token_passes(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/health", headers={"X-API-Token": "t0ken"})
    assert resp.status_code == 200


async def test_query_token_passes_b7(client: httpx.AsyncClient) -> None:
    # B7：EventSource 无法自定义 header，token 经 query param 传递。
    resp = await client.get("/api/health", params={"token": "t0ken"})
    assert resp.status_code == 200


async def test_empty_token_disables_auth(tmp_path: Path, token_env) -> None:
    app = create_app(_settings(tmp_path, ""))
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/health")
            assert resp.status_code == 200
