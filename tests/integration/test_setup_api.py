"""GET/POST /api/setup/* 集成测试：向导 status / complete / check-update。

全部离线：qB 网关以 fake 类替换（路由模块属性注入，同 test_settings_center
手法）；check-update 的 GitHub 外呼以 httpx MockTransport 注入（同
test_rss_preview_api 的 transport 注入手法——仅劫持端点自建的无 transport
client，测试客户端自带的 ASGITransport 不受影响）。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from autoanime.config import Settings, apply_db_overrides, encode_setting_value, parse_db_overrides
from autoanime.core.enums import Actor
from autoanime.core.models import AuditLog
from autoanime.gateway.qbittorrent import GatewayError
from autoanime.web.app import create_app


class _FakeQbitGateway:
    """status 探测用的最小 fake 网关（类属性切可达/不可达）。"""

    fail = False
    save_path: str | None = "C:/downloads"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def version(self) -> str:
        if type(self).fail:
            raise GatewayError("qbittorrent version failed: ConnectError")
        return "v5.1.2"

    async def default_save_path(self) -> str | None:
        return None if type(self).fail else type(self).save_path


@pytest.fixture
async def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    instance = Settings()
    instance.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'setup.db').as_posix()}"
    instance.library_path = tmp_path / "library"
    instance.download_path = tmp_path / "downloads"
    instance.reference_enabled = False
    return instance


@pytest.fixture
async def client(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, Settings, pytest.MonkeyPatch]]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, settings, monkeypatch


# ---------------------------------------------------------------------------
# GET /setup/status
# ---------------------------------------------------------------------------


async def test_status_fresh_install_needed(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """空库：无订阅 → needed；qB 默认 host 非空 → configured；不可达 → False/None/None。"""
    c, _, monkeypatch = client
    monkeypatch.setattr("autoanime.web.routers.setup.QbittorrentGateway", _FakeQbitGateway)
    _FakeQbitGateway.fail = True
    _FakeQbitGateway.save_path = None
    resp = await c.get("/api/setup/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "needed": True,
        "has_subscription": False,
        "downloader_configured": True,  # 默认 host 127.0.0.1 非空
        "downloader_reachable": False,
        "qb_save_path": None,
        "paths_aligned": None,
    }


async def test_status_reachable_and_paths_mismatch(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _, monkeypatch = client
    monkeypatch.setattr("autoanime.web.routers.setup.QbittorrentGateway", _FakeQbitGateway)
    _FakeQbitGateway.fail = False
    _FakeQbitGateway.save_path = "C:/downloads"
    resp = await c.get("/api/setup/status")
    body = resp.json()
    assert body["downloader_reachable"] is True
    assert body["qb_save_path"] == "C:/downloads"
    # 本地 download_path(tmp)/downloads vs C:/downloads → 不一致
    assert body["paths_aligned"] is False
    assert body["needed"] is True


async def test_status_reachable_and_paths_aligned(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, settings, monkeypatch = client
    monkeypatch.setattr("autoanime.web.routers.setup.QbittorrentGateway", _FakeQbitGateway)
    _FakeQbitGateway.fail = False
    _FakeQbitGateway.save_path = str(settings.download_path)
    resp = await c.get("/api/setup/status")
    body = resp.json()
    assert body["qb_save_path"] == str(settings.download_path)
    assert body["paths_aligned"] is True


async def test_status_downloader_not_configured_reachable_is_none(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """host 置空 = 未配置：可达性未探测 → reachable/paths_aligned 为 null。

    直接落 app_settings 行（不依赖 /api/settings PUT——该路由归 settings
    批次所有）；status 的合并读取（apply_db_overrides）对两条路径等价。
    """
    c, _, _ = client
    monkeypatch.setattr("autoanime.web.routers.setup.QbittorrentGateway", _FakeQbitGateway)
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    await app_state.storage.put_app_setting(
        "qbittorrent_host", encode_setting_value("")
    )
    resp = await c.get("/api/setup/status")
    body = resp.json()
    assert body["downloader_configured"] is False
    assert body["downloader_reachable"] is None
    assert body["qb_save_path"] is None
    assert body["paths_aligned"] is None


async def test_status_is_side_effect_free(client, monkeypatch: pytest.MonkeyPatch) -> None:
    c, _, monkeypatch = client
    monkeypatch.setattr("autoanime.web.routers.setup.QbittorrentGateway", _FakeQbitGateway)
    _FakeQbitGateway.fail = False
    for _ in range(2):
        resp = await c.get("/api/setup/status")
        assert resp.status_code == 200
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    audits = await app_state.storage.list(AuditLog)
    # 纯读端点：不写 audit、不落任何表
    assert audits == []


async def test_status_reflects_existing_subscription(client) -> None:
    """有订阅（series 行）→ needed=False（简单诚实口径：使命已达成）。"""
    c, _, _ = client
    resp = await c.post(
        "/api/subscriptions",
        json={"title_cn": "孤独摇滚", "season_number": 1},
    )
    assert resp.status_code == 201, resp.text
    resp = await c.get("/api/setup/status")
    body = resp.json()
    assert body["has_subscription"] is True
    assert body["needed"] is False


# ---------------------------------------------------------------------------
# POST /setup/complete
# ---------------------------------------------------------------------------


async def test_complete_persists_wizard_done_and_audits(client) -> None:
    c, _, _ = client
    resp = await c.post("/api/setup/complete")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}

    app_state = c._transport.app.state  # type: ignore[attr-defined]
    rows = await app_state.storage.list_app_settings()
    assert json.loads(rows["wizard_done"]) is True

    audits = [
        row
        for row in await app_state.storage.list(AuditLog)
        if row.entity == "setup" and row.action == "setup.wizard_done"
    ]
    assert len(audits) == 1
    assert audits[0].actor is Actor.MANUAL
    assert audits[0].instruction == {}


async def test_complete_wizard_done_does_not_leak_into_settings(client) -> None:
    """wizard_done 非 Settings 字段：不进配置合并（parse_db_overrides 跳过未知 key）。

    直接对合并函数断言（不依赖 /api/settings GET——该路由归 settings 批次
    所有）；语义与 status 端点内合并读取一致。
    """
    c, settings, _ = client
    await c.post("/api/setup/complete")
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    rows = await app_state.storage.list_app_settings()
    assert "wizard_done" in rows
    merged = parse_db_overrides(rows)
    assert "wizard_done" not in merged
    # 合并进 Settings 实例不炸、不引入未知属性
    apply_db_overrides(settings.model_copy(), rows)


# ---------------------------------------------------------------------------
# GET /setup/check-update（GitHub 外呼以 MockTransport 注入）
# ---------------------------------------------------------------------------


def _github_payload(tag: str, url: str | None = None) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "html_url": url or f"https://github.com/EstrellaXD/Auto_Bangumi/releases/tag/{tag}",
    }


def _patch_github(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real_client = httpx.AsyncClient

    def _patched(**kwargs: Any) -> httpx.AsyncClient:
        if "transport" not in kwargs:
            kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched)


async def test_check_update_new_version_found(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _, monkeypatch = client

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert "EstrellaXD/Auto_Bangumi" in str(request.url)
        return httpx.Response(
            200, json=_github_payload("v7.2.0")
        )

    _patch_github(monkeypatch, _handler)
    resp = await c.get("/api/setup/check-update")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current"] == "2.0.0.dev0"  # pyproject 安装元数据版本
    assert body["latest"] == "v7.2.0"
    assert body["has_update"] is True
    assert body["changelog_url"].startswith("https://github.com/")
    assert body["error"] is None


async def test_check_update_same_tag_no_update(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _, monkeypatch = client
    monkeypatch.setattr(
        "autoanime.web.routers.setup._current_version", lambda: "v7.2.0"
    )
    _patch_github(monkeypatch, lambda request: httpx.Response(200, json=_github_payload("v7.2.0")))
    resp = await c.get("/api/setup/check-update")
    body = resp.json()
    assert body["has_update"] is False
    assert body["error"] is None


async def test_check_update_empty_tag_no_update(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _, monkeypatch = client
    _patch_github(monkeypatch, lambda request: httpx.Response(200, json={"tag_name": ""}))
    resp = await c.get("/api/setup/check-update")
    body = resp.json()
    assert body["latest"] is None
    assert body["has_update"] is False


async def test_check_update_network_failure_never_500(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _, monkeypatch = client

    def _dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _patch_github(monkeypatch, _dead)
    resp = await c.get("/api/setup/check-update")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_update"] is False
    assert body["error"] == "ConnectError"
    assert body["changelog_url"] is None


async def test_check_update_http_error_never_500(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub 限流 403 → error=HTTPStatusError，绝不 500。"""
    c, _, monkeypatch = client
    _patch_github(monkeypatch, lambda request: httpx.Response(403, json={"message": "rate limited"}))
    resp = await c.get("/api/setup/check-update")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_update"] is False
    assert body["error"] == "HTTPStatusError"


async def test_check_update_cooldown(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """同款进程内冷却（键 setup-check-update）：连发第二次 429。"""
    c, _, monkeypatch = client
    _patch_github(monkeypatch, lambda request: httpx.Response(200, json=_github_payload("v7.2.0")))
    resp = await c.get("/api/setup/check-update")
    assert resp.status_code == 200
    resp = await c.get("/api/setup/check-update")
    assert resp.status_code == 429
