"""gateway.rss 单测（E4a）：条目规范化 / token 拼接 / 失败语义（全部离线）。

- 解析走合成 RSS XML（feedparser 真实路径，不 mock 解析器本身）；
- 网络路径用 ``httpx.MockTransport``；
- token 是密钥：断言拼进请求 URL 但异常文本/对象字符串不含 token。
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from autoanime.gateway.rss import RssFetchError, append_token, fetch_feed, parse_feed

MIKAN_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:bangumi="https://mikanani.me/0.1/">
<channel>
<title>Mikan Project - 我的番剧</title>
<item>
  <guid isPermaLink="false">https://mikanani.me/Download/2024xxx/EF6F.torrent</guid>
  <title>[LoliHouse] 孤独摇滚 - 05 [WebRip 1080p HEVC-10bit AAC]</title>
  <link>https://mikanani.me/Home/Episode/abc</link>
  <enclosure type="application/x-bittorrent" length="372740096"
    url="https://mikanani.me/Download/2024xxx/EF6F.torrent"/>
  <pubDate>Tue, 12 Nov 2024 18:30:00 GMT</pubDate>
</item>
<item>
  <guid>https://mikanani.me/Download/2024xxx/EF70.torrent</guid>
  <title>坏条目无下载链接</title>
  <link>https://mikanani.me/Home/Episode/def</link>
</item>
</channel>
</rss>
"""


def test_parse_feed_normalizes_entries() -> None:
    page = parse_feed(MIKAN_FEED.encode())
    assert page.title == "Mikan Project - 我的番剧"
    assert len(page.entries) == 1  # 缺 enclosure/link 的坏条目被跳过
    entry = page.entries[0]
    assert entry.title.startswith("[LoliHouse] 孤独摇滚 - 05")
    assert entry.torrent_url.endswith("EF6F.torrent")
    assert entry.size == 372740096
    assert entry.published_at is not None
    assert entry.published_at.utcoffset() is not None


def test_parse_feed_garbage_raises() -> None:
    with pytest.raises(RssFetchError):
        parse_feed(b"\x00\x01this is not xml at all")


def test_append_token_merges_once() -> None:
    token = SecretStr("s3cret")
    assert append_token("https://m.host/RSS/MyBangumi", token) == (
        "https://m.host/RSS/MyBangumi?token=s3cret"
    )
    assert append_token("https://m.host/RSS/MyBangumi?token=orig", token) == (
        "https://m.host/RSS/MyBangumi?token=orig"
    )
    assert append_token("https://m.host/RSS/MyBangumi?a=1", token) == (
        "https://m.host/RSS/MyBangumi?a=1&token=s3cret"
    )
    assert append_token("https://m.host/RSS", None) == "https://m.host/RSS"


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_fetch_feed_success_keeps_token_out_of_error_surface() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=MIKAN_FEED.encode())

    async with _client(httpx.MockTransport(handler)) as client:
        page = await fetch_feed(client, "https://mikanani.me/RSS/MyBangumi", token=SecretStr("tkn"))
    assert "token=tkn" in seen["url"]
    assert len(page.entries) == 1


async def test_fetch_feed_http_error_has_no_token_in_text() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"")

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RssFetchError) as exc_info:
            await fetch_feed(client, "https://mikanani.me/RSS/MyBangumi", token=SecretStr("tkn"))
    assert "tkn" not in str(exc_info.value)
    assert exc_info.value.host == "mikanani.me"


async def test_fetch_feed_network_error_is_typed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RssFetchError) as exc_info:
            await fetch_feed(client, "https://blocked.example/RSS")
    assert "ConnectError" in str(exc_info.value)
