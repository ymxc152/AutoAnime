"""gateway.bangumi_calendar subject 别名提取单测（P0-B 别名富化，全离线）。

- ``extract_subject_aliases`` 纯函数直接喂合成 JSON（value 三形态：str /
  list[str] / list[{v}]，去重、剔除主名、空 infobox）；
- ``fetch_subject_aliases``（模块级，显式 client 契约）用 ``httpx.MockTransport``
  覆盖网络路径：HTTP 失败 / 非法 JSON / 非法载荷 → ``BangumiFetchError``；
- ``BangumiCalendarGateway.fetch_subject_aliases``：TTL 缓存命中不重复外呼，
  失败负缓存重抛。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from autoanime.gateway.bangumi_calendar import (
    SUBJECT_TTL_S,
    BangumiCalendarGateway,
    BangumiFetchError,
    extract_subject_aliases,
    fetch_subject_aliases,
)

SUBJECT_JSON: dict[str, Any] = {
    "id": 3281,
    "name": "ヤニねこ",
    "name_cn": "尼古喵喵",
    "infobox": [
        {"key": "中文名", "value": "尼古喵喵"},
        {
            "key": "别名",
            "value": [
                "ヤニねこ",
                "Yani Neko",
                "Chainsmoker Cat",
                {"v": "吸烟猫"},
            ],
        },
        {"key": "话数", "value": "12"},
    ],
}


def test_extract_subject_aliases_list_values_and_dedupe() -> None:
    aliases = extract_subject_aliases(SUBJECT_JSON)
    # 与主名（name/name_cn）相同的项（ヤニねこ）被剔除；{v} 形态取 v；保序去重
    assert aliases == ("Yani Neko", "Chainsmoker Cat", "吸烟猫")


def test_extract_subject_aliases_string_value_splits_on_separators() -> None:
    payload = {
        "name": "Sousou no Frieren",
        "name_cn": "葬送的芙莉莲",
        "infobox": [
            {"key": "别名", "value": "Frieren, 芙莉莲 / 葬送のフリーレン; 葬送"},
        ],
    }
    assert extract_subject_aliases(payload) == (
        "Frieren",
        "芙莉莲",
        "葬送のフリーレン",
        "葬送",
    )


def test_extract_subject_aliases_skips_empty_and_aliases_equal_main() -> None:
    payload = {
        "name": "Bocchi the Rock",
        "name_cn": "孤独摇滚",
        "infobox": [
            # 主名（Bocchi the Rock / 孤独摇滚）不回填；casefold 去重（bocchi 只留一个）
            {"key": "别名", "value": ["孤独摇滚", "bocchi", "  ", "", "Bocchi", "ぼっち・ざ・ろっく"]},
        ],
    }
    assert extract_subject_aliases(payload) == ("bocchi", "ぼっち・ざ・ろっく")


def test_extract_subject_aliases_empty_payload_shapes() -> None:
    # 非 dict / 无 infobox / 无「别名」条 → 空元组（不算失败）
    assert extract_subject_aliases(None) == ()
    assert extract_subject_aliases({"id": 1}) == ()
    assert extract_subject_aliases({"infobox": [{"key": "话数", "value": "12"}]}) == ()


async def test_fetch_subject_aliases_network_contract() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        assert request.headers["User-Agent"]
        return httpx.Response(200, json=SUBJECT_JSON)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        aliases = await fetch_subject_aliases(client, 3281)
    assert calls == ["GET /v0/subjects/3281"]
    assert aliases == ("Yani Neko", "Chainsmoker Cat", "吸烟猫")


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, {}),
        (404, {"error": "not found"}),
    ],
)
async def test_fetch_subject_aliases_http_error_raises(status: int, body: dict) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BangumiFetchError):
            await fetch_subject_aliases(client, 3281)


async def test_fetch_subject_aliases_invalid_payload_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json{")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BangumiFetchError):
            await fetch_subject_aliases(client, 3281)

    def list_payload(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2])

    async with httpx.AsyncClient(transport=httpx.MockTransport(list_payload)) as client:
        with pytest.raises(BangumiFetchError):
            await fetch_subject_aliases(client, 3281)


async def test_gateway_fetch_subject_aliases_caches_and_negative_caches() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("9999"):
            return httpx.Response(500, json={})
        return httpx.Response(200, json=SUBJECT_JSON)

    now = 0.0

    async def _no_sleep(_d: float) -> None:
        return None

    gateway = BangumiCalendarGateway(
        transport=httpx.MockTransport(handler),
        clock=lambda: now,
        sleeper=_no_sleep,
        qps=0.0,
    )
    first = await gateway.fetch_subject_aliases(3281)
    second = await gateway.fetch_subject_aliases(3281)
    assert first == second == ("Yani Neko", "Chainsmoker Cat", "吸烟猫")
    assert calls == ["/v0/subjects/3281"]  # TTL 缓存命中，不重复外呼
    now += SUBJECT_TTL_S + 1.0
    await gateway.fetch_subject_aliases(3281)
    assert calls == ["/v0/subjects/3281", "/v0/subjects/3281"]

    # 失败 → 负缓存：TTL 窗口内不重复外呼，命中时原样重抛
    with pytest.raises(BangumiFetchError):
        await gateway.fetch_subject_aliases(9999)
    with pytest.raises(BangumiFetchError):
        await gateway.fetch_subject_aliases(9999)
    assert calls.count("/v0/subjects/9999") == 1
    await gateway.aclose()
