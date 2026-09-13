"""audit_log.created_at（0008 迁移）+ 环境信息绝对路径契约测试。

覆盖：
- ORM default（``default=datetime.now``）写侧：所有 audit 插入路径
  （governance/learning/organize 均经 ``session.add``）新行自动带时刻，
  显式 None 模拟 0008 迁移前的历史行保持 NULL；
- ``/api/audit`` 明细行 ``created_at``（from_attributes 自动带上）；
- ``/api/audit/operations`` 组级 ``last_created_at`` = 组内 last_audit_id
  行的写入时刻；
- ``/api/settings`` 环境信息新增 ``library_path_abs`` / ``download_path_abs``
  （resolve 后的绝对路径），原 ``library_path`` / ``download_path`` 字段不动。

全部离线（tmp SQLite + metadata.create_all 建库，同 test_api_resources）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from autoanime.config import Settings
from autoanime.core.enums import Actor
from autoanime.core.models import AuditLog
from autoanime.web.app import create_app
from autoanime.web.routers.settings import settings_out


@pytest.fixture
async def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    # 隔离环境变量，保证测试不依赖宿主机 AUTOANIME_* 配置。
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    instance = Settings()
    instance.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'audit_time.db').as_posix()}"
    instance.library_path = tmp_path / "library"
    instance.download_path = tmp_path / "downloads"
    instance.reference_enabled = False  # 单测离线：alias 回填外呼关闭
    instance.api_sse_heartbeat_s = 0.2
    return instance


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[tuple[httpx.AsyncClient, Settings]]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, settings


def _audit_row(operation_id: str, entity_id: int) -> AuditLog:
    """构造一行 audit（不显式给 created_at：走 ORM default，同线上写路径）。"""
    return AuditLog(
        operation_id=operation_id,
        entity="parse_memory",
        entity_id=entity_id,
        action="memory_hit",
        instruction={},
        reverse={},
        actor=Actor.AUTO,
    )


# ---------------------------------------------------------------------------
# 任务 1：created_at（ORM default + /api/audit + /operations）
# ---------------------------------------------------------------------------


async def test_audit_insert_applies_orm_created_at_default(client) -> None:
    """store.add 插入路径（governance.record_audit 同路）新行自动带写入时刻。"""
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    await app_state.storage.add(_audit_row("op-1", 1))
    row = await app_state.storage.get(AuditLog, 1)
    assert row is not None
    assert row.created_at is not None


async def test_audit_page_rows_carry_created_at(client) -> None:
    """明细行 AuditOut 由 from_attributes 自动带上 created_at。"""
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    for index in range(2):
        await app_state.storage.add(_audit_row("op-1", index))

    resp = await c.get("/api/audit", params={"operation_id": "op-1"})
    assert resp.status_code == 200
    page = resp.json()
    assert page["total"] == 2
    for item in page["items"]:
        assert item["created_at"] is not None


async def test_audit_historical_row_created_at_stays_null(client) -> None:
    """历史行（0008 迁移前写入、列为 NULL）明细与组级时间都回 null。

    ORM default 对显式 ``created_at=None`` 也会补时间（实测行为），历史行
    只能用原生 SQL 模拟——这也是迁移给存量行落 NULL 的唯一来源。
    """
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    async with app_state.storage.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO audit_log"
                " (operation_id, entity, entity_id, action, instruction, reverse, actor)"
                " VALUES ('op-hist', 'parse_memory', 1, 'memory_hit', '{}', '{}', 'auto')"
            )
        )

    resp = await c.get("/api/audit")
    assert resp.json()["items"][0]["created_at"] is None
    resp = await c.get("/api/audit/operations")
    group = resp.json()["items"][0]
    assert group["operation_id"] == "op-hist"
    assert group["last_created_at"] is None


async def test_audit_operations_last_created_at(client) -> None:
    """组级 last_created_at = 组内 last_audit_id 那行的写入时刻。"""
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    for index in range(2):
        await app_state.storage.add(_audit_row("op-batch", index))
    await app_state.storage.add(_audit_row("op-single", 9))

    resp = await c.get("/api/audit/operations")
    assert resp.status_code == 200
    page = resp.json()
    assert page["total"] == 2
    batch = next(g for g in page["items"] if g["operation_id"] == "op-batch")
    assert batch["rows"] == 2
    last_row = await app_state.storage.get(AuditLog, batch["last_audit_id"])
    assert last_row is not None and last_row.created_at is not None
    assert batch["last_created_at"] is not None
    assert datetime.fromisoformat(batch["last_created_at"]) == last_row.created_at
    single = next(g for g in page["items"] if g["operation_id"] == "op-single")
    assert single["last_created_at"] is not None


# ---------------------------------------------------------------------------
# 任务 2：环境信息返回绝对路径
# ---------------------------------------------------------------------------


async def test_settings_returns_resolved_absolute_paths(client) -> None:
    """GET /api/settings 新增 *_path_abs = resolve 后绝对路径；原字段不动。"""
    c, settings = client
    resp = await c.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    # resolve 结果 == 预期（与路由同口径：Path(...).resolve()）
    assert body["library_path_abs"] == str(Path(settings.library_path).resolve())
    assert body["download_path_abs"] == str(Path(settings.download_path).resolve())
    assert Path(body["library_path_abs"]).is_absolute()
    assert Path(body["download_path_abs"]).is_absolute()
    # 原有字段一律不动（前端做可选回退依赖原值）
    assert body["library_path"] == str(settings.library_path)
    assert body["download_path"] == str(settings.download_path)


def test_settings_out_resolves_relative_default_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认相对路径（如 ./library）也 resolve 成绝对路径（离线纯函数路径）。"""
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    instance = Settings()
    instance.library_path = Path("library")
    instance.download_path = Path("downloads")
    out = settings_out(instance, {})
    assert out.library_path == "library"
    assert out.library_path_abs == str(Path("library").resolve())
    assert Path(out.library_path_abs).is_absolute()
    assert out.download_path == "downloads"
    assert out.download_path_abs == str(Path("downloads").resolve())
    assert Path(out.download_path_abs).is_absolute()
