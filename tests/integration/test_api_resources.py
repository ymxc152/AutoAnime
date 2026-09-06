"""API 资源集成测试（E2）：全端点走真实 app（内存库 + 关闭参考源外呼）。

覆盖：subscriptions/series CRUD、pending confirm/correct/reject（学习三件套
断言）、audit 分页与分组、rollback 执行引擎、rss_sources CRUD、settings
GET/PUT、metrics 聚合。全部离线。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx
import pytest

from autoanime.config import Settings
from autoanime.core.enums import Actor, MemoryStatus
from autoanime.core.models import (
    AuditLog,
    ParseEvents,
    ParseMemory,
    PendingQueue,
)
from autoanime.memory.governance import MemoryGovernance
from autoanime.pipeline.l3.reference import ReferenceFacts
from autoanime.web.app import create_app


@pytest.fixture
async def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    # 隔离环境变量，保证测试不依赖宿主机 AUTOANIME_* 配置。
    import os

    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    instance = Settings()
    instance.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}"
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


async def _seed_pending(client: httpx.AsyncClient, app_state, raw_name: str, context: dict) -> int:
    del client  # 占位：种子直接走 storage
    row = PendingQueue(raw_name=raw_name, context=context, stage="l3", reason="low confidence")
    await app_state.storage.add(row)
    assert row.id is not None
    return row.id


# ---------------------------------------------------------------------------
# Library / Subscriptions
# ---------------------------------------------------------------------------


async def test_subscription_create_and_series_tree(client) -> None:
    c, _ = client
    resp = await c.post(
        "/api/subscriptions",
        json={"title_cn": "葬送的芙莉莲", "season_number": 1, "episode_count": 3},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title_cn"] == "葬送的芙莉莲"
    assert len(body["seasons"]) == 1
    season = body["seasons"][0]
    assert season["episodes_total"] == 3
    assert season["episodes_missing"] == 3

    resp = await c.get("/api/series")
    assert resp.status_code == 200
    page = resp.json()
    assert page["total"] == 1
    tree = page["items"][0]
    assert tree["seasons"][0]["episodes"][0]["state"] == "missing"
    assert tree["seasons"][0]["episodes"][0]["quality_score"] is None

    series_id = tree["id"]
    resp = await c.get(f"/api/series/{series_id}")
    assert resp.status_code == 200
    resp = await c.get("/api/series/9999")
    assert resp.status_code == 404


async def test_subscription_create_requires_title_and_delete_cascades(client) -> None:
    c, _ = client
    resp = await c.post("/api/subscriptions", json={"season_number": 1})
    assert resp.status_code == 422

    resp = await c.post(
        "/api/subscriptions", json={"title_jp": "葬送のフリーレン", "episode_count": 2}
    )
    assert resp.status_code == 201
    series_id = resp.json()["id"]

    resp = await c.patch(f"/api/subscriptions/{series_id}", json={"fansub_pref": "LoliHouse"})
    assert resp.status_code == 200
    assert resp.json()["fansub_pref"] == "LoliHouse"

    resp = await c.delete(f"/api/subscriptions/{series_id}")
    assert resp.status_code == 204
    assert (await c.get("/api/series")).json()["total"] == 0
    assert (await c.get(f"/api/subscriptions/{series_id}")).status_code == 404


# ---------------------------------------------------------------------------
# Pending：confirm / correct / reject + 学习三件套
# ---------------------------------------------------------------------------


async def test_pending_confirm_learns_parse_memory(client) -> None:
    c, settings = client
    del settings
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    pending_id = await _seed_pending(
        c,
        app_state,
        "[SubsPlease] Sousou no Frieren - 01 (1080p) [mkv]",
        {"title": "Sousou no Frieren", "season": 1, "episode": 1, "fansub": "SubsPlease"},
    )

    resp = await c.post(f"/api/pending/{pending_id}/confirm", json={"title": "葬送的芙莉莲"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["learned_entries"] == 2  # parse_memory 两级
    assert body["bypassed"] is False

    memories = await app_state.storage.list(ParseMemory)
    assert len(memories) == 2
    assert all(row.source == "manual" for row in memories)
    series_row = next(row for row in memories if row.key_level == 1)
    assert series_row.result["title"] == "葬送的芙莉莲"

    # audit + 队列状态
    resp = await c.get("/api/audit", params={"action": "pending_confirm"})
    assert resp.json()["total"] == 1
    resp = await c.get("/api/pending", params={"status": "pending"})
    assert resp.json()["total"] == 0


async def test_pending_confirm_audit_row_counts_as_manual_actor(client) -> None:
    """回归（R2 验收）：人工 confirm 的 audit 行 actor=manual。

    E1 报表口径 manual_intervention_rate = actor==manual 的 audit 行数 /
    archived_events；修复前 pending_audit_row 不带 actor（默认 auto），
    resolved_by=manual 与审计脱节，报表的人工介入率恒 0。
    """
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    pending_id = await _seed_pending(
        c,
        app_state,
        "[SubsPlease] Sousou no Frieren - 02 (1080p) [mkv]",
        {"title": "Sousou no Frieren", "season": 1, "episode": 2, "fansub": "SubsPlease"},
    )
    resp = await c.post(f"/api/pending/{pending_id}/confirm", json={"title": "葬送的芙莉莲"})
    assert resp.status_code == 200, resp.text
    rows = await app_state.storage.list(AuditLog)
    pending_rows = [row for row in rows if row.action == "pending_confirm"]
    assert len(pending_rows) == 1
    assert pending_rows[0].actor == Actor.MANUAL


async def test_pending_confirm_falls_back_to_context_draft(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    pending_id = await _seed_pending(
        c, app_state, "Frieren_EP01_MWeb.mp4", {"title": "Frieren", "episode": 1}
    )
    resp = await c.post(f"/api/pending/{pending_id}/confirm")
    assert resp.status_code == 200
    memories = await app_state.storage.list(ParseMemory)
    exact = next(row for row in memories if row.key_level == 2)
    assert exact.result["episode"] == 1


async def test_pending_confirm_without_title_is_422(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    pending_id = await _seed_pending(c, app_state, "garbage.name.mkv", {"episode": 1})
    resp = await c.post(f"/api/pending/{pending_id}/confirm")
    assert resp.status_code == 422


async def test_pending_correct_triggers_learning_trio(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    raw_name = "[WrongSubs] Sousou no Frieren - 08 [1080p]"
    pending_id = await _seed_pending(
        c, app_state, raw_name, {"title": "Wrong Show", "season": 1, "episode": 8}
    )

    resp = await c.post(
        f"/api/pending/{pending_id}/correct",
        json={"title": "葬送的芙莉莲", "season": 1, "episode": 8, "fansub": "WrongSubs"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["bypassed"] is True

    # 三件套：parse_memory（manual）+ bypass（负记忆）；alias 回填走参考源链
    # （离线测试中以 fake 链路另行断言，见 test_pending_correct_alias_backfill）。
    memories = await app_state.storage.list(ParseMemory)
    assert memories and all(row.source == "manual" for row in memories)
    governance = MemoryGovernance(app_state.storage)
    assert await governance.is_bypassed(raw_name) is True

    # 重复处理已关闭项 → 409
    resp = await c.post(f"/api/pending/{pending_id}/confirm")
    assert resp.status_code == 409


async def test_pending_correct_alias_backfill_via_fake_reference_chain(client, monkeypatch) -> None:
    """接上 fake 参考源链，验证 correct 的 alias 回填分支（title_aliases 写入）。"""
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]

    class FakeChain:
        async def lookup(self, title_shape: str) -> ReferenceFacts | None:
            return ReferenceFacts(
                canonical_title="Sousou no Frieren",
                aliases=("葬送的芙莉莲", "葬送のフリーレン"),
                source="bangumi",
            )

    monkeypatch.setattr(app_state, "reference_chain", FakeChain())
    pending_id = await _seed_pending(
        c, app_state, "[X] Frieren - 01 [1080p]", {"title": "Frieren", "episode": 1}
    )
    resp = await c.post(
        f"/api/pending/{pending_id}/correct", json={"title": "葬送的芙莉莲", "episode": 1}
    )
    assert resp.status_code == 200

    from autoanime.core.models import TitleAlias
    from autoanime.memory.store import SqliteStorage

    del SqliteStorage
    aliases = await app_state.storage.list(TitleAlias)
    by_shape = {row.title_shape_norm: row.canonical_shape for row in aliases}
    # 参考源回填：query/alias 形状 → 参考源 canonical（单一权威）
    reference_targets = {
        shape: canon
        for shape, canon in by_shape.items()
        if shape != "frieren"  # L1 草稿形状（见下）不参与本断言
    }
    assert set(reference_targets.values()) == {"sousou no frieren"}
    assert "葬送的芙莉莲" in reference_targets  # 查询形状本身纳入映射
    # R3 落地：L1 草稿形状（LocalRecognizer 重放 "[X] Frieren - 01 [1080p]"）
    # 映射到确认标题形状——兄弟集经 alias 读侧零外呼命中记忆
    assert by_shape.get("frieren") == "葬送的芙莉莲"


async def test_pending_confirm_after_bypass_writes_nothing(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    raw_name = "[BadSubs] Same Name - 01"
    first = await _seed_pending(c, app_state, raw_name, {"title": "T", "episode": 1})
    assert (await c.post(f"/api/pending/{first}/correct", json={"title": "T2"})).status_code == 200

    second = await _seed_pending(c, app_state, raw_name, {"title": "T", "episode": 1})
    resp = await c.post(f"/api/pending/{second}/confirm")
    assert resp.status_code == 200
    assert resp.json()["bypassed"] is True
    assert resp.json()["learned_entries"] == 0  # bypass 命中：不写记忆（CLI 同口径）


async def test_pending_reject_skips_learning(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    pending_id = await _seed_pending(c, app_state, "reject.me.mkv", {"title": "X", "episode": 1})
    resp = await c.post(f"/api/pending/{pending_id}/reject", json={"reason": "not anime"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"
    assert resp.json()["learned_entries"] == 0
    assert await app_state.storage.list(ParseMemory) == []

    resp = await c.get("/api/pending", params={"status": "skipped"})
    assert resp.json()["total"] == 1
    item = resp.json()["items"][0]
    assert item["resolution"]["reason"] == "not anime"


async def test_pending_list_pagination_and_filter(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    for index in range(3):
        await _seed_pending(c, app_state, f"name-{index}.mkv", {})
    resp = await c.get("/api/pending", params={"limit": 2, "offset": 0})
    page = resp.json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    resp = await c.get("/api/pending", params={"status": "resolved"})
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Audit + rollback
# ---------------------------------------------------------------------------


async def test_audit_page_and_operation_groups(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    for index in range(3):
        await app_state.storage.add(
            AuditLog(
                operation_id="op-batch" if index < 2 else f"op-{index}",
                entity="parse_memory",
                entity_id=index,
                action="memory_hit",
                instruction={},
                reverse={},
                actor=Actor.AUTO,
            )
        )
    resp = await c.get("/api/audit", params={"operation_id": "op-batch"})
    assert resp.json()["total"] == 2
    resp = await c.get("/api/audit/operations")
    groups = resp.json()
    assert groups["total"] == 2
    batch = next(g for g in groups["items"] if g["operation_id"] == "op-batch")
    assert batch["rows"] == 2
    assert batch["actions"] == ["memory_hit"]


async def test_rollback_executes_reverse_and_learns(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    memory = ParseMemory(key_level=1, key_hash="h1", title_shape="shape", result={}, status=MemoryStatus.ACTIVE)
    await app_state.storage.add(memory)
    audit_row = AuditLog(
        operation_id="op-orig",
        entity="parse_memory",
        entity_id=memory.id,
        action="demote_pending",
        instruction={"raw_name": "[G] archived_wrong.mkv"},
        reverse={"status": MemoryStatus.PENDING.value},
        actor=Actor.AUTO,
    )
    await app_state.storage.add(audit_row)

    resp = await c.post(f"/api/organize/{audit_row.id}/rollback")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"]["applied"] == {"status": "pending"}
    assert body["learned"] is True

    restored = await app_state.storage.get(ParseMemory, memory.id)
    assert restored.status is MemoryStatus.PENDING
    governance = MemoryGovernance(app_state.storage)
    assert await governance.is_bypassed("[G] archived_wrong.mkv") is True

    resp = await c.get("/api/audit", params={"action": "rollback"})
    assert resp.json()["total"] == 1


async def test_rollback_rejects_missing_and_empty_reverse(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    resp = await c.post("/api/organize/9999/rollback")
    assert resp.status_code == 404

    empty = AuditLog(
        operation_id="op", entity="episode", entity_id=1, action="archived",
        instruction={}, reverse={}, actor=Actor.AUTO,
    )
    await app_state.storage.add(empty)
    resp = await c.post(f"/api/organize/{empty.id}/rollback")
    assert resp.status_code == 409


async def test_rollback_skips_unsupported_reverse_shapes(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    row = AuditLog(
        operation_id="op", entity="episode", entity_id=1, action="archived",
        instruction={"raw_name": "x.mkv"}, reverse={"moves": [{"from": "a", "to": "b"}]},
        actor=Actor.AUTO,
    )
    await app_state.storage.add(row)
    resp = await c.post(f"/api/organize/{row.id}/rollback")
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"]["applied"] == {}
    assert "moves" in body["applied"]["skipped"]
    assert body["learned"] is True


# ---------------------------------------------------------------------------
# RSS sources（B3）
# ---------------------------------------------------------------------------


async def _create_season(client: httpx.AsyncClient) -> int:
    resp = await client.post("/api/subscriptions", json={"title_cn": "某番"})
    assert resp.status_code == 201
    return resp.json()["seasons"][0]["season_id"]


async def test_rss_source_crud_and_token_hygiene(client) -> None:
    c, _ = client
    season_id = await _create_season(c)

    resp = await c.post(
        "/api/rss_sources",
        json={
            "url": "https://mikanani.me/RSS/MyBangumi?token=rss-secret",
            "token": "rss-secret",
            "season_id": season_id,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["has_token"] is True
    assert "token" not in body  # 独立 token 列永不回显（URL 为用户自存内容）

    source_id = body["id"]
    resp = await c.get("/api/rss_sources")
    assert resp.json()["total"] == 1

    resp = await c.patch(f"/api/rss_sources/{source_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = await c.delete(f"/api/rss_sources/{source_id}")
    assert resp.status_code == 204
    assert (await c.get("/api/rss_sources")).json()["total"] == 0
    assert (await c.delete(f"/api/rss_sources/{source_id}")).status_code == 404


async def test_rss_source_requires_existing_season(client) -> None:
    c, _ = client
    resp = await c.post("/api/rss_sources", json={"url": "https://x/rss", "season_id": 424242})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


async def test_settings_roundtrip_masks_secrets(client) -> None:
    c, settings = client
    resp = await c.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_api_token"] is False
    assert "llm_api_key" not in body
    assert body["l2_enabled"] is True

    resp = await c.put("/api/settings", json={"llm_enabled": True, "dry_run": False})
    assert resp.status_code == 200
    assert resp.json()["llm_enabled"] is True
    assert settings.dry_run is False  # 进程内实例被覆写


async def test_settings_rejects_unknown_payload_keys_semantics(client) -> None:
    c, _ = client
    # 非白名单字段（如 api_port）不允许经 PUT 改（extra=ignore 直接丢弃）。
    resp = await c.put("/api/settings", json={"api_port": 1})
    assert resp.status_code == 200
    assert resp.json()["api_port"] == 8000


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


async def test_metrics_aggregates(client) -> None:
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    today = date.today()
    for row_ in (
        ParseEvents(event_date=today, raw_name_hash="a", level=1, llm_called=False,
                    outcome="archived", confidence="high"),
        ParseEvents(event_date=today, raw_name_hash="b", level=2, llm_called=False,
                    outcome="memory_hit", confidence="medium"),
        ParseEvents(event_date=today, raw_name_hash="c", level=3, llm_called=True,
                    outcome="pending", confidence="low"),
    ):
        await app_state.storage.add(row_)
    await app_state.storage.add(
        AuditLog(operation_id="op", entity="parse_memory", action="memory_hit",
                 instruction={}, reverse={}, actor=Actor.MANUAL)
    )
    await app_state.storage.add(
        AuditLog(operation_id="op", entity="parse_memory", action="memory_hit",
                 instruction={}, reverse={}, actor=Actor.AUTO)
    )
    row = PendingQueue(raw_name="r.mkv", context={}, stage="l3")
    await app_state.storage.add(row)

    resp = await c.get("/api/metrics")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["audit_total"] == 2
    assert body["audit_manual"] == 1
    assert body["intervention_rate"] == 0.5
    levels = {item["level"]: item for item in body["by_level"]}
    assert levels[1]["total"] == 1
    assert levels[3]["llm_called"] == 1
    assert body["pending_open"] == 1
    curve = body["llm_call_curve_weekly"]
    assert len(curve) == 8
    current = curve[-1]
    assert current["total"] == 3
    assert current["llm_rate"] == pytest.approx(1 / 3)
    trend = body["pending_trend_daily"]
    assert len(trend) == 28
    assert trend[-1]["created"] == 1
    assert body["memory_sources"] == []


async def test_rollback_of_episode_row_learns_from_file_field(client) -> None:
    """回归（R1 验收）：episode.organized 行只带 "file" 无 "raw_name"。

    修复前 learned 恒为 False——「回滚即登记错误模式」（5.4）对最常见的
    文件级回滚是死代码；修复后回退取 instruction["file"] 登记 bypass。
    """
    c, _ = client
    app_state = c._transport.app.state  # type: ignore[attr-defined]
    row = AuditLog(
        operation_id="op-import",
        entity="episode",
        entity_id=None,
        action="episode.organized",
        instruction={
            "file": "Bleach.S04E02.1080p.DSNP.WEB-DL.AAC2.0.H.264-MWeb.mkv",
            "dst": "library/Bleach/Season 04/Bleach - S04E02.1080p.mkv",
            "strategy": "hardlink",
            "source": "import",
        },
        reverse={"moves": [{"src": "downloads/x.mkv", "dst": "library/y.mkv",
                            "kind": "hardlink", "role": "video"}]},
        actor=Actor.AUTO,
    )
    await app_state.storage.add(row)

    resp = await c.post(f"/api/organize/{row.id}/rollback")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"]["applied"] == {}  # moves 归 organize 域，v1 端点如实 skipped
    assert body["applied"]["skipped"]["moves"]
    assert body["learned"] is True  # 修复点：从 file 字段学习 bypass

    governance = MemoryGovernance(app_state.storage)
    assert await governance.is_bypassed(
        "Bleach.S04E02.1080p.DSNP.WEB-DL.AAC2.0.H.264-MWeb.mkv"
    ) is True
