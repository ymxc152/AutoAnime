"""选番只读端点集成测试（P1-C）：真实 app + 替换 app.state.bangumi_calendar。

网关本身的外呼行为在 tests/unit/test_gateway_bangumi_calendar.py 离线覆盖；
这里覆盖路由层：200 出参形状、网关失败降级（不 500）、season/year 参数
校验 422、audit 只记年份/季名（无外链 URL）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from autoanime.config import Settings
from autoanime.core.models import AuditLog
from autoanime.gateway.bangumi_calendar import (
    BangumiFetchError,
    BangumiItem,
    SeasonBrowseResult,
)
from autoanime.web.app import create_app


class _StubGateway:
    """路由层测试替身：可注入日历结果 / 异常 / 季浏览结果。"""

    def __init__(
        self,
        *,
        calendar_items: tuple[BangumiItem, ...] = (),
        calendar_error: BangumiFetchError | None = None,
        season_result: SeasonBrowseResult | None = None,
    ) -> None:
        self._calendar_items = calendar_items
        self._calendar_error = calendar_error
        self._season_result = season_result
        self.season_calls: list[tuple[int, str]] = []

    async def fetch_calendar(self) -> tuple[BangumiItem, ...]:
        if self._calendar_error is not None:
            raise self._calendar_error
        return self._calendar_items

    async def fetch_season(self, year: int, season: str) -> SeasonBrowseResult:
        # 当季路由自 v0 搜索路径取数(带 platform/region):注入同 stub 结果
        self.season_calls.append((year, season))
        if self._season_result is not None:
            return self._season_result
        if self._calendar_error is not None:
            return SeasonBrowseResult(items=[], degraded=True, reason=self._calendar_error.detail)
        return SeasonBrowseResult(items=self._calendar_items, degraded=False, reason=None)

    async def aclose(self) -> None:
        return None


def _item(subject_id: int, title_cn: str) -> BangumiItem:
    return BangumiItem(
        subject_id=subject_id,
        title_cn=title_cn,
        title_jp=f"JP {subject_id}",
        image_url=f"https://lain.bgm.tv/pic/cover/c/{subject_id}.jpg",
        rating=8.0,
        air_date="2026-07-05",
        eps=12,
        mikan_search_url=f"https://mikanani.me/Home/Search?searchstr={title_cn}",
        platform="TV",
        region="jp",
    )


@pytest.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    # 隔离环境变量，保证测试不依赖宿主机 AUTOANIME_* 配置。
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    settings = Settings()
    settings.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'cal.db').as_posix()}"
    settings.library_path = tmp_path / "library"
    settings.reference_enabled = False
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


async def test_season_calendar_returns_items(env) -> None:
    client, app = env
    stub = _StubGateway(calendar_items=(_item(1, "葬送的芙莉莲"), _item(2, "孤独摇滚！")))
    app.state.bangumi_calendar = stub
    resp = await client.get("/api/season-calendar")
    assert stub.season_calls, "当季应走 fetch_season(v0 搜索路径,带 platform/region)"
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is False
    assert body["reason"] is None
    assert len(body["items"]) == 2
    first = body["items"][0]
    assert first["subject_id"] == 1
    assert first["title_cn"] == "葬送的芙莉莲"
    assert first["mikan_search_url"].startswith("https://mikanani.me/Home/Search?searchstr=")


async def test_season_calendar_gateway_failure_degrades_not_500(env) -> None:
    client, app = env
    app.state.bangumi_calendar = _StubGateway(
        calendar_error=BangumiFetchError("api.bgm.tv", "http 502")
    )
    resp = await client.get("/api/season-calendar")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is True
    assert body["items"] == []
    assert body["reason"] == "http 502"


async def test_season_browse_valid_and_invalid_params(env) -> None:
    client, app = env
    stub = _StubGateway(season_result=SeasonBrowseResult(items=(_item(7, "夏日条目"),), degraded=False, reason=None))
    app.state.bangumi_calendar = stub

    resp = await client.get("/api/season-browse", params={"year": 2026, "season": "summer"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"][0]["title_cn"] == "夏日条目"
    assert stub.season_calls == [(2026, "summer")]

    # 非法季名 / 越界年份 → 422（FastAPI 枚举与 Query(ge=1990) 校验）。
    for params in (
        {"year": 2026, "season": "dry"},
        {"year": 1899, "season": "winter"},
        {"season": "winter"},
    ):
        resp = await client.get("/api/season-browse", params=params)
        assert resp.status_code == 422, f"{params}: {resp.text}"

    # year 超过 当前年+1 → 422（上限在路由内手动校验）。
    from datetime import UTC, datetime

    too_far = datetime.now(UTC).year + 2
    resp = await client.get("/api/season-browse", params={"year": too_far, "season": "fall"})
    assert resp.status_code == 422, resp.text


async def test_season_browse_degraded_result_passes_through(env) -> None:
    client, app = env
    app.state.bangumi_calendar = _StubGateway(
        season_result=SeasonBrowseResult(items=(), degraded=True, reason="http 500")
    )
    resp = await client.get("/api/season-browse", params={"year": 2024, "season": "fall"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is True
    assert body["reason"] == "http 500"


async def test_calendar_endpoints_write_audit_without_external_urls(env) -> None:
    client, app = env
    app.state.bangumi_calendar = _StubGateway(
        calendar_items=(_item(1, "葬送的芙莉莲"),),
        season_result=SeasonBrowseResult(items=(), degraded=False, reason=None),
    )
    await client.get("/api/season-calendar")
    await client.get("/api/season-browse", params={"year": 2025, "season": "spring"})
    rows = list(await app.state.storage.list(AuditLog))
    actions = {row.action for row in rows}
    assert "season_calendar_viewed" in actions
    assert "season_browse_viewed" in actions
    browse = next(row for row in rows if row.action == "season_browse_viewed")
    assert browse.instruction == {"year": 2025, "season": "spring"}
    # 审计里不出现任何外链 URL。
    assert "mikanani.me" not in str(rows)
