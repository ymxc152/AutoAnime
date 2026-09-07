"""12-D 配置中心 API 集成测试：白名单三档 / 密钥纪律 / 测试端点 / 持久化。

全部离线：notify-test / qbit-test 的外呼以 fake 通道类替换（路由模块
属性注入），存储走真实 app + tmp SQLite。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from autoanime.config import Settings
from autoanime.core.enums import Actor
from autoanime.core.models import AuditLog
from autoanime.gateway.qbittorrent import GatewayError
from autoanime.web.app import create_app


@pytest.fixture
async def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    instance = Settings()
    instance.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'settings.db').as_posix()}"
    instance.library_path = tmp_path / "library"
    instance.reference_enabled = False  # 单测离线：alias 回填外呼关闭
    instance.api_sse_heartbeat_s = 0.2
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
# GET：字段面 + 密钥只回 has_*
# ---------------------------------------------------------------------------


async def test_get_settings_masks_all_secrets(client) -> None:
    c, _, _ = client
    resp = await c.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    # 任何密钥字段不出现在载荷里，只有 has_* 布尔
    for key in (
        "llm_api_key",
        "tmdb_api_key",
        "qbittorrent_password",
        "notify_webhook_url",
        "notify_telegram_bot_token",
    ):
        assert key not in body
    assert body["has_llm_api_key"] is False
    assert body["has_tmdb_api_key"] is False
    assert body["has_qbittorrent_password"] is False
    assert body["has_notify_webhook_url"] is False
    assert body["has_notify_telegram_bot_token"] is False
    # 12-D 新增字段面（三档各抽代表）
    assert body["scheduler_enabled"] is True
    assert body["rss_poll_interval_minutes"] == 30
    assert body["collected_check_days"] == 30
    assert body["downloader"] == "qbittorrent"
    assert body["upgrade_threshold"] == 2.0
    assert body["naming_title_language"] == "title_cn"
    assert body["llm_base_url"] is None


# ---------------------------------------------------------------------------
# PUT：三档生效标注 + 白名单外 422
# ---------------------------------------------------------------------------


async def test_put_annotates_effect_tiers(client) -> None:
    c, settings, _ = client
    resp = await c.put(
        "/api/settings",
        json={
            "dry_run": False,
            "rss_poll_interval_minutes": 15,
            "qbittorrent_host": "192.0.2.1",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] == {
        "dry_run": "immediate",
        "rss_poll_interval_minutes": "scheduler_rebuild",
        "qbittorrent_host": "requires_restart",
    }
    # immediate/scheduler 档：进程内即时可见
    assert body["dry_run"] is False
    assert body["rss_poll_interval_minutes"] == 15
    assert settings.dry_run is False
    assert settings.rss_poll_interval_minutes == 15
    # restart 档：只落库（GET 显示待生效值），运行时实例不动
    assert body["qbittorrent_host"] == "192.0.2.1"
    assert settings.qbittorrent_host == "127.0.0.1"


async def test_put_outside_whitelist_is_422(client) -> None:
    c, _, _ = client
    resp = await c.put("/api/settings", json={"api_port": 1})
    assert resp.status_code == 422
    resp = await c.put("/api/settings", json={"library_path": "/tmp"})
    assert resp.status_code == 422


async def test_scheduler_tier_put_triggers_rebuild_hook(client) -> None:
    c, _, monkeypatch = client
    calls: list[Any] = []

    async def fake_rebuild(state: Any, settings: Settings) -> list[str]:
        calls.append(state)
        return []

    monkeypatch.setattr(
        "autoanime.web.routers.settings.rebuild_running_scheduler", fake_rebuild
    )
    resp = await c.put("/api/settings", json={"rss_poll_interval_minutes": 45})
    assert resp.status_code == 200
    assert resp.json()["warnings"] == []
    assert len(calls) == 1

    # 仅 immediate/restart 档不触发重建
    calls.clear()
    resp = await c.put("/api/settings", json={"dry_run": True, "downloader": "aria2"})
    assert resp.status_code == 200
    assert calls == []


# ---------------------------------------------------------------------------
# PUT：密钥语义（空串不改 / null 清除）+ audit 无密钥值
# ---------------------------------------------------------------------------


async def test_secret_put_empty_keeps_null_clears(client) -> None:
    c, _, _ = client
    resp = await c.put("/api/settings", json={"llm_api_key": "sk-test-123"})
    assert resp.json()["has_llm_api_key"] is True
    assert resp.json()["applied"] == {"llm_api_key": "requires_restart"}

    # 空串 = 不修改（不进 applied）
    resp = await c.put("/api/settings", json={"llm_api_key": ""})
    assert resp.status_code == 200
    assert resp.json()["has_llm_api_key"] is True
    assert resp.json()["applied"] == {}

    # 显式 null = 清除
    resp = await c.put("/api/settings", json={"llm_api_key": None})
    assert resp.status_code == 200
    assert resp.json()["has_llm_api_key"] is False


async def test_settings_audit_records_keys_not_values(client) -> None:
    c, _, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    resp = await c.put(
        "/api/settings",
        json={"llm_api_key": "super-secret-value", "dry_run": False},
    )
    assert resp.status_code == 200

    rows = [
        row
        for row in await app_state.storage.list(AuditLog)
        if row.entity == "settings" and row.action == "settings.updated"
    ]
    assert len(rows) == 1
    instruction = rows[0].instruction
    assert "super-secret-value" not in json.dumps(instruction)
    assert instruction["keys"] == ["dry_run", "llm_api_key"]
    assert instruction["effects"]["llm_api_key"] == "requires_restart"
    assert rows[0].actor is Actor.MANUAL


# ---------------------------------------------------------------------------
# 持久化：PUT 落库 → 新进程（新 Settings 实例）启动合并
# ---------------------------------------------------------------------------


async def test_settings_persist_and_merge_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    db_url = f"sqlite+aiosqlite:///{(tmp_path / 'restart.db').as_posix()}"

    def make_settings() -> Settings:
        return Settings(
            database_url=db_url,
            library_path=tmp_path / "library",
            reference_enabled=False,
            api_sse_heartbeat_s=0.2,
        )

    first = make_settings()
    app1 = create_app(first)
    transport1 = httpx.ASGITransport(app=app1)
    async with app1.router.lifespan_context(app1):
        async with httpx.AsyncClient(transport=transport1, base_url="http://test") as c:
            resp = await c.put(
                "/api/settings",
                json={
                    "rss_poll_interval_minutes": 45,
                    "llm_base_url": "http://example.invalid/v1",
                    "downloader": "aria2",
                    "llm_api_key": "sk-persisted",
                },
            )
            assert resp.status_code == 200, resp.text

    # 「重启」：全新 Settings 实例 + 全新 app，env/toml → DB 覆盖合并
    second = make_settings()
    assert second.rss_poll_interval_minutes == 30  # 合并前仍是默认
    app2 = create_app(second)
    transport2 = httpx.ASGITransport(app=app2)
    async with app2.router.lifespan_context(app2):
        async with httpx.AsyncClient(transport=transport2, base_url="http://test") as c:
            body = (await c.get("/api/settings")).json()
            assert body["rss_poll_interval_minutes"] == 45
            assert body["llm_base_url"] == "http://example.invalid/v1"
            assert body["downloader"] == "aria2"
            assert body["has_llm_api_key"] is True
            # 合并结果反映在运行时实例上（loop 装配读同一对象）
            assert second.rss_poll_interval_minutes == 45
            assert second.llm_base_url == "http://example.invalid/v1"
            assert second.downloader == "aria2"
            assert second.llm_api_key is not None


# ---------------------------------------------------------------------------
# notify-test（fake 通道，成功/失败两路 + 未配置空明细）
# ---------------------------------------------------------------------------


class _FakeNotifier:
    """notify.py 两个 Notifier 的替身（记录调用，按 fail 标记抛错）。"""

    fail = False
    sent: list[Any] = []
    created = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).created += 1

    async def send_or_raise(self, event: Any) -> None:
        if type(self).fail:
            raise RuntimeError("channel down")
        type(self).sent.append(event)


@pytest.fixture
def fake_notify_channels(monkeypatch: pytest.MonkeyPatch) -> dict[str, type[_FakeNotifier]]:
    webhook = type("FakeWebhookNotifier", (_FakeNotifier,), {"fail": False, "sent": [], "created": 0})
    telegram = type("FakeTelegramNotifier", (_FakeNotifier,), {"fail": False, "sent": [], "created": 0})
    monkeypatch.setattr("autoanime.web.routers.settings.WebhookNotifier", webhook)
    monkeypatch.setattr("autoanime.web.routers.settings.TelegramNotifier", telegram)
    return {"webhook": webhook, "telegram": telegram}


async def test_notify_test_reports_per_channel(client, fake_notify_channels) -> None:
    c, _, _ = client
    webhook = fake_notify_channels["webhook"]
    telegram = fake_notify_channels["telegram"]

    # 通道配置走 PUT（restart 档，只落库）——notify-test 必须按待生效值试跑
    resp = await c.put(
        "/api/settings",
        json={
            "notify_webhook_url": "http://hook.local/notify",
            "notify_telegram_bot_token": "tok",
            "notify_telegram_chat_id": "42",
        },
    )
    assert resp.status_code == 200, resp.text

    resp = await c.post("/api/settings/notify-test")
    assert resp.status_code == 200, resp.text
    results = {item["channel"]: item for item in resp.json()["results"]}
    assert results["webhook"]["ok"] is True
    assert results["telegram"]["ok"] is True
    assert webhook.created == 1 and telegram.created == 1
    assert len(webhook.sent) == 1 and len(telegram.sent) == 1

    # 失败路：单通道异常只归因到该通道（error 只含异常类型名）
    # （试跑端点有进程内冷却；重置 app.state 时间戳模拟冷却已过）
    c._transport.app.state._settings_test_calls = {}  # type: ignore[attr-defined]
    webhook.fail = True
    resp = await c.post("/api/settings/notify-test")
    results = {item["channel"]: item for item in resp.json()["results"]}
    assert results["webhook"]["ok"] is False
    assert results["webhook"]["error"] == "RuntimeError"
    assert results["telegram"]["ok"] is True

    # 动作落 audit（不记 URL/token）
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    rows = [
        row
        for row in await app_state.storage.list(AuditLog)
        if row.entity == "settings" and row.action == "settings.notify_test"
    ]
    assert len(rows) == 2
    assert "hook.local" not in json.dumps(rows[0].instruction)
    assert "tok" not in json.dumps(rows[0].instruction)


async def test_notify_test_without_channels_returns_empty(client) -> None:
    c, _, _ = client
    resp = await c.post("/api/settings/notify-test")
    assert resp.status_code == 200
    assert resp.json()["results"] == []


async def test_test_endpoints_rate_limited(client) -> None:
    """试跑端点冷却：冷却期内连发返回 429，不触发外呼。"""
    c, _, _ = client
    resp = await c.post("/api/settings/notify-test")
    assert resp.status_code == 200
    resp = await c.post("/api/settings/notify-test")
    assert resp.status_code == 429
    resp = await c.post("/api/settings/qbit-test")
    # qbit-test 与 notify-test 冷却相互独立
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# qbit-test（fake 网关，成功/失败两路）
# ---------------------------------------------------------------------------


class _FakeQbitGateway:
    fail = False
    init_args: tuple[tuple[Any, ...], dict[str, Any]] = ((), {})

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).init_args = (args, kwargs)

    async def version(self) -> str:
        if type(self).fail:
            raise GatewayError("qbittorrent version failed: ConnectError")
        return "v5.1.2"


async def test_qbit_test_success(client, monkeypatch: pytest.MonkeyPatch) -> None:
    c, _, _ = client
    monkeypatch.setattr(
        "autoanime.web.routers.settings.QbittorrentGateway", _FakeQbitGateway
    )
    _FakeQbitGateway.fail = False
    resp = await c.post("/api/settings/qbit-test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True, "version": "v5.1.2", "error": None}
    args, kwargs = _FakeQbitGateway.init_args
    # 合并配置（此处 = 默认 Settings）传入网关构造
    assert args == ("127.0.0.1", 8080, "admin", Settings().qbittorrent_password)
    assert kwargs["category"] == "autoanime"

    app_state = c._transport.app.state  # type: ignore[attr-defined]
    rows = [
        row
        for row in await app_state.storage.list(AuditLog)
        if row.entity == "settings" and row.action == "settings.qbit_test"
    ]
    assert len(rows) == 1
    assert rows[0].instruction == {"ok": True}


async def test_qbit_test_failure_returns_reason(client, monkeypatch: pytest.MonkeyPatch) -> None:
    c, _, _ = client
    monkeypatch.setattr(
        "autoanime.web.routers.settings.QbittorrentGateway", _FakeQbitGateway
    )
    _FakeQbitGateway.fail = True
    resp = await c.post("/api/settings/qbit-test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "qbittorrent version failed: ConnectError"

    app_state = c._transport.app.state  # type: ignore[attr-defined]
    rows = [
        row
        for row in await app_state.storage.list(AuditLog)
        if row.entity == "settings" and row.action == "settings.qbit_test"
    ]
    assert rows[-1].instruction == {"ok": False, "error": "GatewayError"}


# ---------------------------------------------------------------------------
# asgi 全链路：调度类 PUT → 真 scheduler loop 重建（不重启进程）
# ---------------------------------------------------------------------------


def test_asgi_scheduler_rebuild_on_scheduler_tier_put(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from autoanime.scheduler.asgi import create_app as create_asgi_app

    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'asgi-rebuild.db').as_posix()}",
        library_path=tmp_path / "library",
        download_path=tmp_path / "downloads",
        quarantine_path=tmp_path / "quarantine",
        downloader="aria2",
        aria2_rpc_url="http://127.0.0.1:1/jsonrpc",  # 不可达（离线，惰性客户端）
        reference_enabled=False,
    )
    app = create_asgi_app(settings)
    with TestClient(app) as client:
        old_scheduler = app.state.scheduler
        assert old_scheduler.running is True

        resp = client.put("/api/settings", json={"rss_poll_interval_minutes": 7})
        assert resp.status_code == 200, resp.text
        assert resp.json()["warnings"] == []

        new_scheduler = app.state.scheduler
        assert new_scheduler is not old_scheduler
        assert old_scheduler.running is False
        assert new_scheduler.running is True  # 重建后不中断调度
        assert settings.rss_poll_interval_minutes == 7  # 新 loop 按新间隔装配
    assert new_scheduler.running is False
