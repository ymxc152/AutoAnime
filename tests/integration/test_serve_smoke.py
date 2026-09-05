"""serve 真实起服冒烟（E2 验收）：uvicorn 子进程 + httpx 全资源冒烟 + SSE 实测。

- 离线纪律：服务器仅绑 127.0.0.1，AUTOANIME_REFERENCE_ENABLED=false 关闭
  alias 回填外呼，全程无真实外网；
- SSE 以真实 TCP 流验证（httpx ASGITransport 会整体缓冲响应，无法承载
  无限流），心跳/在线事件/Last-Event-ID 回放各一；
- 本文件同时是验收报告中「uvicorn 起服 + httpx 冒烟」的证据来源。
"""

from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    """起一个真实 uvicorn 子进程，yield base_url；结束终止进程。"""
    tmp_path = tmp_path_factory.mktemp("serve")
    db_path = tmp_path / "smoke.db"
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "AUTOANIME_API_SSE_HEARTBEAT_S": "0.2",
            "AUTOANIME_REFERENCE_ENABLED": "false",
            "AUTOANIME_API_TOKEN": "",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    env = {key: value for key, value in env.items() if not key.startswith("AUTOANIME_LLM")}
    log_path = tmp_path / "server.log"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "autoanime.api",
            "serve",
            "--port",
            str(port),
            "--db",
            f"sqlite+aiosqlite:///{db_path.as_posix()}",
        ],
        cwd=_REPO_ROOT,
        env=env,
        stdout=open(log_path, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(f"{base_url}/api/health", timeout=1.0)
                if resp.status_code == 200:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
            time.sleep(0.3)
            assert process.poll() is None, f"server exited early; log: {log_path.read_text(encoding='utf-8')}"
        else:
            raise AssertionError(f"server not ready in time: {last_error}")
        yield base_url, db_path
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def test_smoke_all_resources(server) -> None:
    base_url, db_path = server
    with httpx.Client(base_url=base_url, timeout=10) as client:
        # 订阅创建（series/season/episode 预生成）
        resp = client.post(
            "/api/subscriptions",
            json={"title_cn": "冒烟番", "season_number": 1, "episode_count": 3},
        )
        assert resp.status_code == 201, resp.text
        subscription = resp.json()
        season_id = subscription["seasons"][0]["season_id"]

        resp = client.get("/api/series")
        assert resp.status_code == 200 and resp.json()["total"] == 1

        # RSS 源（B3）
        resp = client.post(
            "/api/rss_sources",
            json={
                "url": "https://mikanime.tv/RSS/MyBangumi",
                "token": "smoke",
                "season_id": season_id,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["has_token"] is True
        resp = client.get("/api/rss_sources")
        assert resp.json()["total"] == 1

        # Settings GET/PUT
        resp = client.put("/api/settings", json={"llm_enabled": True})
        assert resp.status_code == 200 and resp.json()["llm_enabled"] is True
        resp = client.get("/api/settings")
        assert resp.json()["llm_enabled"] is True

        # 直接向 SQLite 播种 pending 行（pending 由识别管线写入，E2 无造数端点）
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.execute(
                "INSERT INTO pending_queue (raw_name, context, stage, status, resolution, created_at)"
                " VALUES (?, ?, ?, 'pending', NULL, datetime('now'))",
                ("[SmokeSubs] Smoke Show - 01 [1080p]", "{}", "l3"),
            )
            conn.commit()
            pending_id = conn.execute("SELECT max(id) FROM pending_queue").fetchone()[0]

        # confirm → 学习三件套（parse_memory 两级；参考源关闭 → alias 回填跳过）
        resp = client.post(
            f"/api/pending/{pending_id}/confirm", json={"title": "冒烟番"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["learned_entries"] == 2

        # rollback：播种带 reverse 的审计行 + parse_memory 行
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.execute(
                "INSERT INTO parse_memory (key_level, key_hash, result, source,"
                " hit_count, corrected_count, status)"
                " VALUES (1, 'smokehash', '{}', 'manual', 0, 0, 'active')"
            )
            memory_id = conn.execute("SELECT max(id) FROM parse_memory").fetchone()[0]
            conn.execute(
                "INSERT INTO audit_log (operation_id, entity, entity_id, action,"
                " instruction, reverse, actor)"
                " VALUES ('smoke-op', 'parse_memory', ?, 'demote_pending', '{}',"
                " '{\"status\": \"pending\"}', 'auto')",
                (memory_id,),
            )
            conn.commit()
            audit_id = conn.execute("SELECT max(id) FROM audit_log").fetchone()[0]

        resp = client.post(f"/api/organize/{audit_id}/rollback")
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"]["applied"] == {"status": "pending"}

        # audit + metrics 收尾
        resp = client.get("/api/audit", params={"limit": 200})
        assert resp.status_code == 200
        actions = {item["action"] for item in resp.json()["items"]}
        assert {"pending_confirm", "rollback", "subscription_created", "rss_source_created"} <= actions
        resp = client.get("/api/audit/operations")
        assert resp.status_code == 200 and resp.json()["total"] >= 1
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        assert resp.json()["pending_open"] == 0


def test_smoke_sse_heartbeat_live_and_replay(server) -> None:
    base_url, _ = server
    # 触发线程：连接建立后更新订阅 → 总线发布 SYSTEM 事件 → SSE 在线帧。
    subscription_id_holder: list[int] = []

    with httpx.Client(base_url=base_url, timeout=10) as client:
        resp = client.post(
            "/api/subscriptions", json={"title_cn": "SSE 番", "season_number": 1}
        )
        assert resp.status_code == 201
        subscription_id_holder.append(resp.json()["id"])

    def trigger() -> None:
        time.sleep(0.5)
        with httpx.Client(base_url=base_url, timeout=10) as client:
            client.patch(
                f"/api/subscriptions/{subscription_id_holder[0]}",
                json={"fansub_pref": "SmokeSubs"},
            )

    thread = threading.Thread(target=trigger)
    thread.start()
    frames: list[str] = []
    try:
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(5, read=3)) as client:
            with client.stream("GET", "/api/events") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                for line in response.iter_lines():
                    frames.append(line)
                    if line.startswith("event: system"):
                        break
    finally:
        thread.join(timeout=5)

    assert "retry: 3000" in frames  # 重连提示
    assert any(line == ": heartbeat" for line in frames), frames  # 心跳注释帧
    system_index = frames.index("event: system")
    # SSE 编码序为 id → event → data：审计行 id 出现在 event 行之前的相邻帧里。
    assert any(line.startswith("id: ") for line in frames[max(0, system_index - 3) : system_index])

    # Last-Event-ID 回放：以 id=0 连接 → 落库审计行按序补发。
    frames: list[str] = []
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(5, read=3)) as client:
        with client.stream(
            "GET", "/api/events", headers={"Last-Event-ID": "0"}
        ) as response:
            for line in response.iter_lines():
                frames.append(line)
                if line.startswith("data:"):
                    break
    assert "id: 1" in frames  # 首条审计行按 id 升序回放
