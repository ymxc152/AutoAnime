"""POST /api/pipeline/rss-preview 集成测试：真实 app + MockTransport feed。

覆盖：命中/同集择优(would_download 唯一)/规则排除/异番冲突/无命中 404 语义
前置(至少一个标题 422)/上游失败 502。零落库断言(库无 release 行)。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from autoanime.config import Settings
from autoanime.web.app import create_app

FEED_XML = """<rss><channel><title>generic feed</title>
<item><guid>g1</guid><title>[LoliHouse] 孤独摇滚 - 01 [Baha 1080p][简中]</title>
  <enclosure type='application/x-bittorrent' length='100' url='https://example.com/t/a'/></item>
<item><guid>g2</guid><title>[SubPl] 孤独摇滚 - 01 [720p HDTV]</title>
  <enclosure type='application/x-bittorrent' length='100' url='https://example.com/t/b'/></item>
<item><guid>g3</guid><title>[LoliHouse] 孤独摇滚 - 02 [内嵌广告版][简中]</title>
  <enclosure type='application/x-bittorrent' length='100' url='https://example.com/t/c'/></item>
<item><guid>g4</guid><title>[别的组] 完全无关的另一部番 - 01 [1080p]</title>
  <enclosure type='application/x-bittorrent' length='100' url='https://example.com/t/d'/></item>
</channel></rss>"""


@pytest.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    settings = Settings()
    settings.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'preview.db').as_posix()}"
    settings.library_path = tmp_path / "library"
    settings.reference_enabled = False

    def _feed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=FEED_XML)

    # 预览端点自建 httpx.AsyncClient:仅当调用方没带 transport(=端点的外呼)
    # 时注入 MockTransport;测试客户端自带 ASGITransport,不能劫持
    real_client = httpx.AsyncClient

    def _patched_client(**kwargs: Any) -> httpx.AsyncClient:
        if "transport" not in kwargs:
            kwargs["transport"] = httpx.MockTransport(_feed_handler)
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched_client)

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "rss_url": "https://example.com/rss?token=supersecret",
        "title_cn": "孤独摇滚",
        "title_jp": "ぼっち・ざ・ろっく!",
        "season_number": 1,
        "exclude_keywords": "内嵌广告",
    }
    body.update(overrides)
    return body


async def test_rss_preview_marks_best_per_episode(env) -> None:
    client, _app = env
    resp = await client.post("/api/pipeline/rss-preview", json=_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["entries_total"] == 4
    assert data["download_count"] == 1
    by_title = {e["title"]: e for e in data["entries"]}
    # 同集两条:评分最优的 LoliHouse 1080p 标 would_download,720p 停在 candidate
    assert by_title["[LoliHouse] 孤独摇滚 - 01 [Baha 1080p][简中]"]["verdict"] == "would_download"
    assert by_title["[SubPl] 孤独摇滚 - 01 [720p HDTV]"]["verdict"] == "candidate"
    # 规则排除
    assert by_title["[LoliHouse] 孤独摇滚 - 02 [内嵌广告版][简中]"]["verdict"] == "rejected"
    assert "excluded_by_rule" in (
        by_title["[LoliHouse] 孤独摇滚 - 02 [内嵌广告版][简中]"]["reason"] or ""
    )
    # 异番冲突拒绝
    assert by_title["[别的组] 完全无关的另一部番 - 01 [1080p]"]["verdict"] == "rejected"
    assert "expected_conflict" in (
        by_title["[别的组] 完全无关的另一部番 - 01 [1080p]"]["reason"] or ""
    )
    # 密钥纪律:token 不出现在任何响应文本里
    assert "supersecret" not in resp.text


async def test_rss_preview_requires_a_title(env) -> None:
    client, _app = env
    resp = await client.post("/api/pipeline/rss-preview", json=_body(title_cn=None, title_jp=None))
    assert resp.status_code == 422


async def test_rss_preview_upstream_failure_is_502(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _dead_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    real_client = httpx.AsyncClient

    def _patched(**kwargs: Any) -> httpx.AsyncClient:
        if "transport" not in kwargs:
            kwargs["transport"] = httpx.MockTransport(_dead_handler)
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched)
    client, _app = env
    resp = await client.post("/api/pipeline/rss-preview", json=_body())
    assert resp.status_code == 502


async def test_rss_preview_writes_nothing_to_db(env) -> None:
    client, app = env
    resp = await client.post("/api/pipeline/rss-preview", json=_body())
    assert resp.status_code == 200
    # 零落库:audit/releases 表都不新增(预览端点不记审计、不建 release)
    from autoanime.core.models import AuditLog, ReleaseRecord

    async with app.state.storage.transaction() as session:
        audit = (await session.execute(__import__("sqlalchemy").select(AuditLog))).scalars().all()
        releases = (
            (await session.execute(__import__("sqlalchemy").select(ReleaseRecord))).scalars().all()
        )
    assert audit == []
    assert releases == []
