"""12-F 后端补端点集成测试：单源轮询 / confirm-name / reparse / report。

覆盖：

- ``POST /api/rss_sources/{id}/poll``：成功（含 skipped_not_due 与离线
  fetch_error 两态）/ 404 / disabled 409；
- ``POST /api/pipeline/confirm-name``：成功（学习 + pending 收尾 +
  hardlink 归档）/ 422（非法 segment / extra=forbid）/ 与 CLI confirm
  的等价性（同一库上两条入口输出同构、落库效果一致）；
- ``POST /api/episodes/{id}/reparse``：dry-run 两态成功 / 404 / 源文件
  不在位 409 / extra=forbid 422；
- ``GET /api/report``：与 ``cli._aggregate_report``（CLI --json 真源）
  的结构一致性 + 空库口径。

全部离线：不触网络（RSS/aria2 指向不可达端口，失败路径如实断言）。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import pytest

from autoanime import cli
from autoanime.cli import main as cli_main
from autoanime.config import Settings
from autoanime.core.enums import Actor, EpisodeState, MemoryStatus, SeasonState
from autoanime.core.models import AuditLog, ParseEvents, ParseMemory, PendingQueue, Season
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l2.keys import KEY_LEVEL_SERIES, key_hash, level1_key
from autoanime.scheduler.store import LoopStore, TransitionError
from autoanime.web.app import create_app


@pytest.fixture
async def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    # 隔离环境变量，保证测试不依赖宿主机 AUTOANIME_* 配置。
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    value = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / '12f.db').as_posix()}",
        library_path=tmp_path / "library",
        download_path=tmp_path / "downloads",
        quarantine_path=tmp_path / "quarantine",
        downloader="aria2",
        aria2_rpc_url="http://127.0.0.1:1/jsonrpc",
        reference_enabled=False,
        rss_fetch_retries=0,  # 离线失败路径不退避（零 sleep）
        api_sse_heartbeat_s=0.2,
    )
    value.download_path.mkdir(parents=True, exist_ok=True)
    return value


@pytest.fixture
async def client(
    settings: Settings,
) -> AsyncIterator[tuple[httpx.AsyncClient, Settings]]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, settings


def _app_state(c: httpx.AsyncClient) -> Any:
    return c._transport.app.state  # type: ignore[attr-defined]


async def _create_subscription_and_source(
    c: httpx.AsyncClient, *, title: str = "12F测试番"
) -> tuple[dict, dict]:
    resp = await c.post(
        "/api/subscriptions",
        json={"title_cn": title, "season_number": 1, "episode_count": 1},
    )
    assert resp.status_code == 201, resp.text
    series = resp.json()
    # SubscriptionOut.seasons 是进度口径（season_id）；POST /api/rss_sources
    # 需要的是 season 表 id，这里直接取进度载荷里的 season_id。
    season_id = series["seasons"][0]["season_id"]
    resp = await c.post(
        "/api/rss_sources", json={"url": "http://127.0.0.1:1/rss", "season_id": season_id}
    )
    assert resp.status_code == 201, resp.text
    return series, resp.json()


# ---------------------------------------------------------------------------
# 任务 1：POST /api/rss_sources/{id}/poll
# ---------------------------------------------------------------------------


async def test_poll_source_success_not_due(client) -> None:
    """UPCOMING 季不轮询（cadence 与 rerun 同判定）：200 + skipped_not_due。"""
    c, _ = client
    _, source = await _create_subscription_and_source(c)
    resp = await c.post(f"/api/rss_sources/{source['id']}/poll")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_id"] == source["id"]
    assert body["skipped_not_due"] is True
    assert body["picked"] == 0
    assert body["download"]["checked"] == 0
    # audit + SSE 事件留痕
    app_state = _app_state(c)
    audits, total = await app_state.api_store.list_audit_page(
        action="rss_source_polled", operation_id=None, entity=None, limit=10, offset=0
    )
    assert total == 1


async def test_poll_source_fetch_error_offline(client) -> None:
    """AIRING 季 + 不可达 RSS：如实记 fetch_error，不 5xx（与 rerun 同路径）。"""
    c, _ = client
    series, source = await _create_subscription_and_source(c)
    app_state = _app_state(c)
    async with app_state.storage.transaction() as session:
        season = await session.get(Season, series["seasons"][0]["season_id"])
        season.status = SeasonState.AIRING
    resp = await c.post(f"/api/rss_sources/{source['id']}/poll")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["skipped_not_due"] is False
    assert body["fetch_error"] == "unreachable after retries"
    assert body["entries_total"] == 0
    assert body["picked"] == 0


async def test_poll_source_404_and_disabled_409(client) -> None:
    c, _ = client
    resp = await c.post("/api/rss_sources/9999/poll")
    assert resp.status_code == 404

    _, source = await _create_subscription_and_source(c)
    resp = await c.patch(
        f"/api/rss_sources/{source['id']}", json={"enabled": False}
    )
    assert resp.status_code == 200
    resp = await c.post(f"/api/rss_sources/{source['id']}/poll")
    assert resp.status_code == 409
    assert "disabled" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 任务 2：POST /api/pipeline/confirm-name
# ---------------------------------------------------------------------------


async def test_confirm_name_learns_resolves_and_archives(client) -> None:
    c, settings = client
    app_state = _app_state(c)
    raw_name = "Frieren - 01.mkv"
    (settings.download_path / raw_name).write_bytes(b"x")
    await app_state.storage.add(
        PendingQueue(
            raw_name=raw_name,
            context={"parent_path": str(settings.download_path)},
            stage="import",
            reason="seed",
        )
    )
    resp = await c.post(
        "/api/pipeline/confirm-name",
        json={"name": raw_name, "title": "葬送的芙莉莲", "season": 1, "episode": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bypassed"] is False
    assert body["resolved_pending"] == 1
    assert body["archive"]["archived"] is True
    assert body["entries"]
    # hardlink 归档落库（D17 命名），源文件保留（D21）
    archived = settings.library_path / "葬送的芙莉莲" / "Season 01"
    assert list(archived.rglob("*.mkv"))
    assert (settings.download_path / raw_name).exists()
    # pending 行被收尾（与 CLI confirm 同语义）
    resp = await c.get("/api/pending")
    assert resp.json()["items"][0]["status"] == "resolved"
    # 审计留痕（pending_confirm 惯例）
    _, total = await app_state.api_store.list_audit_page(
        action="pending_confirm", operation_id=None, entity=None, limit=10, offset=0
    )
    assert total >= 1


async def test_confirm_name_422_invalid_segment_and_extra_field(client) -> None:
    c, _ = client
    resp = await c.post(
        "/api/pipeline/confirm-name",
        json={"name": "x.mkv", "title": "T", "segment": "bogus"},
    )
    assert resp.status_code == 422
    resp = await c.post(
        "/api/pipeline/confirm-name",
        json={"name": "x.mkv", "title": "T", "wat": 1},
    )
    assert resp.status_code == 422  # extra=forbid


async def test_confirm_name_equivalent_to_cli_confirm(tmp_path: Path, monkeypatch) -> None:
    """等价性（12-F）：CLI confirm 与 confirm-name 同库同构输出、同落库效果。"""
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    db = tmp_path / "eq.db"
    library = tmp_path / "library"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setenv("AUTOANIME_DATABASE_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    monkeypatch.setenv("AUTOANIME_LIBRARY_PATH", library.as_posix())
    monkeypatch.setenv("AUTOANIME_LLM_ENABLED", "false")
    monkeypatch.setenv("AUTOANIME_REFERENCE_ENABLED", "false")
    names = ["EqOne - 01.mkv", "EqTwo - 02.mkv"]
    for name in names:
        (downloads / name).write_bytes(b"x")
    url = f"sqlite+aiosqlite:///{db.as_posix()}"
    async with SqliteStorage(url) as storage:
        await storage.create_all()
        for name in names:
            await storage.add(
                PendingQueue(
                    raw_name=name,
                    context={"parent_path": str(downloads)},
                    stage="import",
                    reason="seed",
                )
            )

    def _run_cli(*args: str) -> tuple[int, str]:
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli_main(list(args))
        return code, out.getvalue()

    code, cli_out = await asyncio.to_thread(
        _run_cli,
        "confirm", "--name", names[0],
        "--title", "等价验证", "--season", "1", "--episode", "1",
    )
    assert code == 0
    cli_payload = json.loads(cli_out)

    settings = Settings()  # 读回上面 setenv 的同一套配置
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/pipeline/confirm-name",
                json={"name": names[1], "title": "等价验证", "season": 1, "episode": 2},
            )
    assert resp.status_code == 200, resp.text
    web_payload = resp.json()

    # 输出结构与 CLI confirm --json 同字段口径
    assert set(web_payload) == set(cli_payload)
    assert web_payload["bypassed"] is False and cli_payload["bypassed"] is False
    assert web_payload["resolved_pending"] == cli_payload["resolved_pending"] == 1
    assert web_payload["archive"]["archived"] and cli_payload["archive"]["archived"]
    assert {e["key_level"] for e in web_payload["entries"]} == {
        e["key_level"] for e in cli_payload["entries"]
    }
    # 两条入口都在库中 hardlink 归档（D17 命名，D21 保留原件）
    assert list(library.rglob("*S01E01*.mkv")), cli_payload
    assert list(library.rglob("*S01E02*.mkv")), web_payload
    for name in names:
        assert (downloads / name).exists()


# ---------------------------------------------------------------------------
# 任务 3：POST /api/episodes/{id}/reparse
# ---------------------------------------------------------------------------


async def _seed_organized_episode(
    c: httpx.AsyncClient, settings: Settings, *, with_file: bool = True,
    name: str = "Show S01E01 1080p.mkv",
) -> tuple[int, Path, int]:
    resp = await c.post(
        "/api/subscriptions",
        json={"title_cn": "重新识别番", "season_number": 1, "episode_count": 1},
    )
    assert resp.status_code == 201, resp.text
    series_id = resp.json()["id"]
    # EpisodeOut 从 series 树（GET /api/series/{id}）取：那里才有 episode id。
    resp = await c.get(f"/api/series/{series_id}")
    assert resp.status_code == 200, resp.text
    tree = resp.json()
    episode = tree["seasons"][0]["episodes"][0]
    src = settings.download_path / name
    if with_file:
        src.write_bytes(b"0" * 64)
    app_state = _app_state(c)
    store = LoopStore(app_state.storage)
    await store.update_episode_archive_state(
        episode["id"], target=EpisodeState.ORGANIZED, file_path=str(src)
    )
    return episode["id"], src, series_id


async def test_reparse_dry_run_previews_without_moving(client) -> None:
    c, settings = client
    episode_id, src, _ = await _seed_organized_episode(c, settings)
    resp = await c.post(f"/api/episodes/{episode_id}/reparse", json={"dry_run": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["episode_id"] == episode_id
    assert body["dry_run"] is True
    assert body["parsed"]["episode"] == 1
    assert body["action"]["action"] == "archive"
    assert body["action"]["dst"].endswith(".mkv")
    # 预览零副作用：文件不动、库不变
    assert src.exists()
    assert not list(settings.library_path.rglob("*.mkv"))


async def test_reparse_executes_archive_and_updates_pointer(client) -> None:
    c, settings = client
    episode_id, src, series_id = await _seed_organized_episode(c, settings)
    resp = await c.post(f"/api/episodes/{episode_id}/reparse", json={"dry_run": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is False
    assert body["action"]["action"] == "archive"
    dst = Path(str(body["action"]["dst"]))
    assert dst.exists()
    # episode 状态/文件指针经 store 状态机落地
    resp = await c.get(f"/api/series/{series_id}")
    ep = resp.json()["seasons"][0]["episodes"][0]
    assert ep["state"] == "organized"
    assert ep["file_path"] == str(dst)
    # audit 与 organize 同口径（episode.organized + source=reparse）
    app_state = _app_state(c)
    audits, total = await app_state.api_store.list_audit_page(
        action="episode.organized", operation_id=None, entity=None, limit=10, offset=0
    )
    assert total == 1
    assert audits[0].instruction.get("source") == "reparse"


async def test_reparse_404_and_missing_source_409(client) -> None:
    c, settings = client
    resp = await c.post("/api/episodes/9999/reparse", json={"dry_run": True})
    assert resp.status_code == 404

    episode_id, _src, _sid = await _seed_organized_episode(
        c, settings, with_file=False
    )
    for dry_run in (True, False):
        resp = await c.post(
            f"/api/episodes/{episode_id}/reparse", json={"dry_run": dry_run}
        )
        assert resp.status_code == 409, (dry_run, resp.text)
        assert "source file not present" in resp.json()["detail"]


async def test_reparse_422_extra_field(client) -> None:
    c, _ = client
    resp = await c.post(
        "/api/episodes/1/reparse", json={"dry_run": True, "extra": 1}
    )
    assert resp.status_code == 422  # extra=forbid


async def test_reparse_dry_run_writes_no_db_rows(client) -> None:
    """缺陷 2：dry-run 预览零落库——命中 L2 记忆也不递增 hit_count、不写 audit。"""
    c, settings = client
    episode_id, _src, _sid = await _seed_organized_episode(
        c, settings, name="Frieren - 01.mkv"
    )
    app_state = _app_state(c)
    # 预置系列级记忆：dry-run 的 L2 会命中（fuse → HIGH → arbitration）。
    await app_state.storage.add(
        ParseMemory(
            key_level=KEY_LEVEL_SERIES,
            key_hash=key_hash(level1_key("Frieren")),
            result={
                "title": "葬送的芙莉莲",
                "season": 1,
                "episode": 1,
                "segment": "episode",
                "fansub": None,
            },
            status=MemoryStatus.ACTIVE,
            hit_count=0,
            corrected_count=0,
        )
    )
    resp = await c.post(f"/api/episodes/{episode_id}/reparse", json={"dry_run": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # L2 命中照常参与预览（返回结果不被破坏）：season 由记忆行补全。
    assert body["parsed"]["season"] == 1
    assert body["parsed"]["evidence"].get("season") == "memory"
    # 零 DB 写入：hit_count 未递增、无 arbiter / parse_memory audit 行。
    row = await app_state.storage.find_parse_memory(
        KEY_LEVEL_SERIES, key_hash(level1_key("Frieren"))
    )
    assert row is not None and row.hit_count == 0
    audits = await app_state.storage.list(AuditLog)
    assert not [a for a in audits if a.entity == "arbiter"]
    assert not [a for a in audits if a.entity == "parse_memory"]


async def test_reparse_rolls_back_when_archive_update_transition_fails(
    client, monkeypatch
) -> None:
    """缺陷 1 防御路径：落库状态机拒绝时回滚搬移、指针不被污染、409 如实。"""
    c, settings = client
    episode_id, src, _sid = await _seed_organized_episode(c, settings)

    async def _broken_update(
        self, episode_id, *, target, file_path=None,
        quality_score=None, upgraded_count_delta=0,
    ):
        # 模拟并发窗口内落库被状态机拒绝（如另一并发把集置成不可归档态）。
        raise TransitionError(f"episode {episode_id}: DOWNLOADING -> organized illegal")

    monkeypatch.setattr(LoopStore, "update_episode_archive_state", _broken_update)
    resp = await c.post(f"/api/episodes/{episode_id}/reparse", json={"dry_run": False})
    assert resp.status_code == 409
    # 补偿：目标位文件已回滚，episode.file_path 仍是原指针，未被污染。
    assert src.exists()
    assert not list(settings.library_path.rglob("*.mkv"))
    app_state = _app_state(c)
    store = LoopStore(app_state.storage)
    ep = await store.get_episode(episode_id)
    assert ep is not None
    assert ep.file_path == str(src)


async def test_reparse_preexec_guard_rejects_changed_state(client, monkeypatch) -> None:
    """缺陷 1 主修复：搬移前预检重读最新状态，并发改态后拒绝且不动文件。"""
    c, settings = client
    episode_id, src, _sid = await _seed_organized_episode(c, settings)
    real_get = LoopStore.get_episode
    calls = {"n": 0}

    async def _state_flips_after_router(_self, _episode_id: int):
        calls["n"] += 1
        row = await real_get(_self, _episode_id)
        assert row is not None
        # 第二次读取（搬移前预检）模拟并发把集改成不可归档态。
        if calls["n"] == 2:
            row.state = EpisodeState.IGNORED
        return row

    monkeypatch.setattr(LoopStore, "get_episode", _state_flips_after_router)
    resp = await c.post(f"/api/episodes/{episode_id}/reparse", json={"dry_run": False})
    assert resp.status_code == 409
    assert "cannot be (re)organized" in resp.json()["detail"]
    # 预检失败未搬移文件。
    assert src.exists()
    assert not list(settings.library_path.rglob("*.mkv"))


# ---------------------------------------------------------------------------
# 任务 4：GET /api/report
# ---------------------------------------------------------------------------


async def test_report_empty_db_matches_cli_zero_state(client) -> None:
    c, _ = client
    resp = await c.get("/api/report")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generated_from"] == {"parse_events": 0, "audit_log": 0}
    assert body["manual_intervention_rate"]["rate"] is None


async def test_report_structure_matches_cli_aggregation(client) -> None:
    c, _ = client
    app_state = _app_state(c)
    await app_state.storage.add(
        ParseEvents(
            event_date=date(2026, 9, 1),
            raw_name_hash="hash-a",
            level=2,
            llm_called=False,
            latency_ms=12,
            outcome="archive",
        )
    )
    await app_state.storage.add(
        ParseEvents(
            event_date=date(2026, 9, 1),
            raw_name_hash="hash-b",
            level=1,
            llm_called=True,
            outcome="l3",
        )
    )
    await app_state.storage.add(
        AuditLog(
            operation_id="op-1",
            entity="parse_memory",
            action="correct",
            instruction={},
            reverse={},
            actor=Actor.MANUAL,
        )
    )
    events = await app_state.storage.list(ParseEvents)
    audits = await app_state.storage.list(AuditLog)
    expected = json.loads(
        json.dumps(cli._aggregate_report(events, audits), ensure_ascii=False, default=str)
    )
    resp = await c.get("/api/report")
    assert resp.status_code == 200, resp.text
    assert resp.json() == expected
