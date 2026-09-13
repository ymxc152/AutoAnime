"""gateway.bangumi_calendar 单测（P1-C）：映射 / 降级链 / 频控缓存（全离线）。

- 映射纯函数直接喂合成 JSON（不 mock 映射器本身）；
- 网络路径用 ``httpx.MockTransport``，clock/sleeper 注入零等待假时钟；
- 缓存命中/负缓存用请求计数断言「不重复外呼」。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from autoanime.gateway.bangumi_calendar import (
    CALENDAR_TTL_S,
    MAX_SEASON_PAGES,
    NEGATIVE_TTL_S,
    SEASON_PAGE_LIMIT,
    BangumiCalendarGateway,
    BangumiFetchError,
    map_calendar_response,
)

CALENDAR_JSON = [
    {
        "weekday": {"id": 1, "en": "Mon"},
        "items": [
            {
                "id": 101,
                "name": "Sousou no Frieren",
                "name_cn": "葬送的芙莉莲",
                "images": {"common": "https://lain.bgm.tv/pic/cover/c/101.jpg"},
                "rating": {"score": 9.2},
                "air_date": "2026-07-05",
                "eps": 28,
            },
            {"id": 102, "name": "No CN Title", "images": {}, "rating": None},
            {"name": "缺 id 的坏条目"},
        ],
    },
    {"weekday": {"id": 2, "en": "Tue"}, "items": []},
]


def _gateway(handler: httpx.MockTransport, *, clock=None, sleeper=None) -> BangumiCalendarGateway:
    return BangumiCalendarGateway(
        transport=handler, clock=clock, sleeper=sleeper, qps=0.0
    )


def _body(request: httpx.Request) -> dict[str, Any]:
    """解析 POST 请求体（坏体当空 dict，便于断言 filter 字段）。"""
    try:
        parsed = json.loads(request.content or b"{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def test_map_calendar_response_flattens_and_skips_bad() -> None:
    items = map_calendar_response(CALENDAR_JSON)
    assert len(items) == 2  # 缺 id 的坏条目被跳过
    first = items[0]
    assert first.subject_id == 101
    assert first.title_cn == "葬送的芙莉莲"
    assert first.title_jp == "Sousou no Frieren"
    assert first.image_url == "https://lain.bgm.tv/pic/cover/c/101.jpg"
    assert first.rating == 9.2
    assert first.air_date == "2026-07-05"
    assert first.eps == 28
    # 有中文名 → 用中文名搜 Mikan。
    assert first.mikan_search_url.endswith(f"searchstr={quote('葬送的芙莉莲')}")
    second = items[1]
    assert second.title_cn is None
    # 无中文名 → 回退日文名。
    assert second.mikan_search_url.endswith(f"searchstr={quote('No CN Title')}")
    assert second.rating is None
    assert second.image_url is None


def test_map_calendar_response_rejects_non_list() -> None:
    with pytest.raises(ValueError):
        map_calendar_response({"unexpected": True})


async def test_fetch_calendar_maps_and_caches() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=CALENDAR_JSON)

    now = 0.0
    gateway = _gateway(
        httpx.MockTransport(handler), clock=lambda: now, sleeper=lambda _d: None
    )
    items = await gateway.fetch_calendar()
    assert len(items) == 2
    await gateway.fetch_calendar()
    assert calls == ["GET /calendar"]  # 缓存命中，不重复外呼
    now += CALENDAR_TTL_S + 1.0  # 过期后再拉
    await gateway.fetch_calendar()
    assert calls == ["GET /calendar", "GET /calendar"]
    await gateway.aclose()


async def test_fetch_calendar_negative_cache() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append("GET")
        return httpx.Response(500, json={})

    now = 0.0
    gateway = _gateway(
        httpx.MockTransport(handler), clock=lambda: now, sleeper=lambda _d: None
    )
    with pytest.raises(BangumiFetchError) as first:
        await gateway.fetch_calendar()
    assert "http 500" in str(first.value)
    with pytest.raises(BangumiFetchError):
        await gateway.fetch_calendar()
    assert calls == ["GET"]  # 负缓存命中，未重复外呼
    now += NEGATIVE_TTL_S + 1.0
    with pytest.raises(BangumiFetchError):
        await gateway.fetch_calendar()
    assert calls == ["GET", "GET"]  # 负缓存过期后重试
    await gateway.aclose()


def _season_subject(subject_id: int, date: str) -> dict:
    return {
        "id": subject_id,
        "name": f"Subject {subject_id}",
        "name_cn": f"条目{subject_id}",
        "images": {"common": f"https://lain.bgm.tv/pic/cover/c/{subject_id}.jpg"},
        "rating": {"score": 7.0},
        "date": date,
        "eps": 12,
    }


def _page(data: list[dict], total: int, offset: int) -> httpx.Response:
    return httpx.Response(200, json={"total": total, "limit": 50, "offset": offset, "data": data})


async def test_fetch_season_degrade_chain_attempt1_air_date_filter() -> None:
    """档 1 生效：带 air_date 过滤的请求 200 → 客户端再按月区间精确过滤。"""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(_body(request))
        return _page(
            [
                _season_subject(1, "2026-01-10"),
                _season_subject(2, "2026-03-31"),
                _season_subject(3, "2026-04-01"),  # 出区间：被客户端过滤
                _season_subject(4, "2025-12-31"),  # 出区间：被客户端过滤
            ],
            total=4,
            offset=0,
        )

    gateway = _gateway(httpx.MockTransport(handler))
    result = await gateway.fetch_season(2026, "winter")
    assert not result.degraded
    assert [item.subject_id for item in result.items] == [1, 2]
    assert bodies[0]["filter"]["air_date"] == [">=2026-01-01", "<=2026-03-31"]
    await gateway.aclose()


async def test_fetch_season_degrade_chain_attempt1_fails_attempt2_paginates() -> None:
    """档 1 → 档 2：air_date 语法 400 后，仅 type 过滤翻页 + date 过滤生效。"""
    bodies: list[dict] = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = _body(request)
        bodies.append(body)
        if "air_date" in (body.get("filter") or {}):
            return httpx.Response(400, json={"error": "bad filter"})
        offset = int(body.get("offset", 0))
        assert body.get("limit") == SEASON_PAGE_LIMIT
        if offset == 0:
            data = [_season_subject(i, "2026-05-01") for i in range(1, SEASON_PAGE_LIMIT + 1)]
            return _page(data, total=SEASON_PAGE_LIMIT + 2, offset=0)
        data = [
            _season_subject(100, "2026-06-30"),
            _season_subject(101, "2026-07-01"),  # 夏季，被过滤
        ]
        return _page(data, total=SEASON_PAGE_LIMIT + 2, offset=offset)

    gateway = _gateway(httpx.MockTransport(handler))
    result = await gateway.fetch_season(2026, "spring")
    assert not result.degraded
    assert calls == 3  # 档 1 一次 + 档 2 翻两页
    # 第一页 50 条全部 2026-05-01 在区间内 + 第二页 6-30 在区间内，7-1 被过滤。
    assert len(result.items) == SEASON_PAGE_LIMIT + 1
    assert all(item.air_date is not None and "2026-04-01" <= item.air_date[:10] <= "2026-06-30" for item in result.items)
    await gateway.aclose()


async def test_fetch_season_all_attempts_fail_returns_degraded() -> None:
    """档 1/2 全 500 → 档 3 降级空结果（不抛异常、不 500）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    gateway = _gateway(httpx.MockTransport(handler))
    result = await gateway.fetch_season(2026, "summer")
    assert result.degraded
    assert result.items == ()
    assert result.reason == "http 500"
    await gateway.aclose()


async def test_fetch_season_pagination_cap_returns_partial() -> None:
    """翻页达上限：返回已过滤的部分结果（degraded=False，不无限翻页）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        if "air_date" in (body.get("filter") or {}):
            return httpx.Response(400, json={})
        data = [_season_subject(i, "2026-01-15") for i in range(1, SEASON_PAGE_LIMIT + 1)]
        return _page(data, total=10**9, offset=int(body.get("offset", 0)))

    gateway = _gateway(httpx.MockTransport(handler))
    result = await gateway.fetch_season(2026, "winter")
    assert not result.degraded
    assert len(result.items) == MAX_SEASON_PAGES * SEASON_PAGE_LIMIT
    await gateway.aclose()


async def test_fetch_season_cache_hit_no_extra_calls() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = _body(request)
        if "air_date" in (body.get("filter") or {}):
            date = "2026-02-02" if "01-01" in str(body.get("filter")) else "2026-07-10"
            return _page([_season_subject(1, date)], total=1, offset=0)
        return httpx.Response(500, json={})

    gateway = _gateway(httpx.MockTransport(handler))
    first = await gateway.fetch_season(2026, "winter")
    second = await gateway.fetch_season(2026, "winter")
    assert calls == 1
    assert first == second
    # 不同季不共享缓存。
    other = await gateway.fetch_season(2026, "summer")
    assert not other.degraded
    assert [item.air_date for item in other.items] == ["2026-07-10"]
    await gateway.aclose()


# ---------- platform/region 字段(选番页地区/特别篇过滤的数据源) ----------


def test_derive_region_from_tags() -> None:
    from autoanime.gateway.bangumi_calendar import derive_region

    assert derive_region([{"name": "日本", "count": 999}]) == "jp"
    assert derive_region([{"name": "原创", "count": 5}, {"name": "中国", "count": 88}]) == "cn"
    assert derive_region(["国产", "玄幻"]) == "cn"
    assert derive_region([{"name": "韩国", "count": 3}]) == "kr"
    assert derive_region([{"name": "北美", "count": 3}]) == "us"
    assert derive_region([{"name": "原创", "count": 3}]) is None
    assert derive_region(None) is None


def test_map_subject_carries_platform_and_region() -> None:
    from autoanime.gateway.bangumi_calendar import map_subject

    raw = {
        "id": 123,
        "name": "テスト",
        "name_cn": "测试",
        "platform": "OVA",
        "tags": [{"name": "日本", "count": 50}],
    }
    item = map_subject(raw)
    assert item is not None
    assert item.platform == "OVA"
    assert item.region == "jp"

    bare = map_subject({"id": 124, "name": "最低限"})
    assert bare is not None
    assert bare.platform is None
    assert bare.region is None
