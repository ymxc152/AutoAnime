"""RSS 拉取网关（E4 M4 闭环）：Mikan / 自配源 → 规范条目。

边界（铁律 3）：本模块是 RSS 的唯一网络出口。httpx 拉字节（async，代理
环境经 trust_env 复用 HTTPS_PROXY——Mikan 主站部分地区被墙的实操对策），
feedparser 是同步库，一律 ``asyncio.to_thread`` 包裹（审核 B6，否则阻塞
事件循环、SSE 心跳停摆）。

密钥纪律：RSS token（如 Mikan ``?token=xxx``）以 ``SecretStr`` 传入，
拼进请求 URL 但**绝不进日志/异常文本/审计**——异常只带 host 与状态码。
失败语义：网络错误/超时/非 200/空 feed → ``RssFetchError``，由轮询器
重试后跳过本轮（不 crash 不告警风暴），条目解析失败跳过该条目。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import feedparser
import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)

#: enclosure 的类型标记（Mikan 的 torrent 附件）；不做严格白名单，仅优先级提示。
_TORRENT_MIME_HINTS = ("application/x-bittorrent", "application/octet-stream")

_USER_AGENT = "AutoAnime/2.0 (+https://github.com/autoanime) local-first rss reader"


class RssFetchError(Exception):
    """拉取或解析失败；文本不含 token/密钥。"""

    def __init__(self, host: str, detail: str) -> None:
        super().__init__(f"rss fetch failed ({host}): {detail}")
        self.host = host
        self.detail = detail


@dataclass(frozen=True)
class RssEntry:
    """规范化后的 RSS 条目（FlexGet 条目语义的最小集：title/link/guid）。"""

    title: str
    torrent_url: str
    guid: str
    published_at: datetime | None
    size: int | None


@dataclass(frozen=True)
class FeedPage:
    """一次拉取的规范化结果。"""

    url_host: str
    title: str | None
    entries: tuple[RssEntry, ...]


def append_token(url: str, token: SecretStr | None) -> str:
    """把 token 拼进 query（``?token=``）；None 原样返回。

    Mikan 的私有 RSS 是 ``https://host/RSS/MyBangumi?token=xxx`` 形态；
    token 已在配置的 URL 里时不必重复传。
    """
    if token is None or not token.get_secret_value():
        return url
    parts = urlsplit(url)
    query = parts.query
    if "token=" in query:
        return url
    merged = f"{query}&token={token.get_secret_value()}" if query else f"token={token.get_secret_value()}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, merged, parts.fragment))


def _entry_torrent_url(entry: dict[str, Any]) -> str | None:
    """优先 enclosure（.torrent 附件）；link 仅在明显是 .torrent 地址时兜底。

    Mikan 的 ``<link>`` 是剧集页（HTML），真正的种子地址在 ``<enclosure>``；
    把 HTML 页当种子地址会污染下游下载流程，故 link 只认 ``.torrent`` 后缀。
    """
    for enclosure in entry.get("enclosures") or []:
        href = enclosure.get("href")
        if not href:
            continue
        mime = str(enclosure.get("type") or "").lower()
        if mime in _TORRENT_MIME_HINTS or str(href).lower().endswith(".torrent"):
            return str(href)
    link = entry.get("link")
    if link and str(link).lower().endswith(".torrent"):
        return str(link)
    return None


def _entry_published(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def normalize_entries(parsed: Any) -> tuple[RssEntry, ...]:
    """feedparser 解析结果 → 规范条目；缺 title/url/guid 的条目跳过。"""
    entries: list[RssEntry] = []
    for entry in parsed.get("entries") or []:
        title = entry.get("title")
        torrent_url = _entry_torrent_url(entry)
        guid = entry.get("id") or torrent_url
        if not title or not torrent_url or not guid:
            logger.debug("rss entry skipped: missing title/url/guid")
            continue
        size_raw = None
        for enclosure in entry.get("enclosures") or []:
            length = enclosure.get("length")
            if length is not None:
                try:
                    size_raw = int(str(length))
                    break
                except ValueError:
                    continue
        entries.append(
            RssEntry(
                title=str(title),
                torrent_url=torrent_url,
                guid=str(guid),
                published_at=_entry_published(entry),
                size=size_raw,
            )
        )
    return tuple(entries)


def parse_feed(data: bytes) -> FeedPage:
    """同步解析（调用方放 to_thread）：bozo 只在零条目时才判失败。"""
    parsed = feedparser.parse(data)
    entries = normalize_entries(parsed)
    if parsed.get("bozo") and not entries:
        detail = str(parsed.get("bozo_exception") or "unparsable feed")
        raise RssFetchError(host="<memory>", detail=detail)
    feed = parsed.get("feed") or {}
    title = feed.get("title")
    return FeedPage(url_host="", title=str(title) if title else None, entries=entries)


async def fetch_feed(
    client: httpx.AsyncClient, url: str, *, token: SecretStr | None = None
) -> FeedPage:
    """拉取并解析一个 RSS 源。失败抛 ``RssFetchError``（不含 token）。"""
    request_url = append_token(url, token)
    host = urlsplit(url).netloc or "unknown"
    try:
        response = await client.get(
            request_url, headers={"User-Agent": _USER_AGENT}, follow_redirects=True
        )
    except httpx.HTTPError as exc:
        raise RssFetchError(host, type(exc).__name__) from None
    if response.status_code != 200:
        raise RssFetchError(host, f"http {response.status_code}")
    try:
        return await asyncio.to_thread(parse_feed, response.content)
    except RssFetchError:
        raise
    except Exception as exc:  # feedparser 偶发非标准异常
        raise RssFetchError(host, f"parse: {type(exc).__name__}") from None


#: .torrent 字节数上限（防误配 HTML 页/超大文件拖垮内存）。
MAX_TORRENT_BYTES = 10 * 1024 * 1024


async def fetch_torrent(client: httpx.AsyncClient, url: str) -> bytes:
    """取回 .torrent 字节（infohash 在 gateway.torrents 离线计算）。

    失败语义同 feed：``RssFetchError``（不含 token）；内容超限/空体按失败。
    """
    host = urlsplit(url).netloc or "unknown"
    try:
        response = await client.get(
            url, headers={"User-Agent": _USER_AGENT}, follow_redirects=True
        )
    except httpx.HTTPError as exc:
        raise RssFetchError(host, type(exc).__name__) from None
    if response.status_code != 200:
        raise RssFetchError(host, f"http {response.status_code}")
    data = response.content
    if not data or len(data) > MAX_TORRENT_BYTES:
        raise RssFetchError(host, f"torrent size out of range: {len(data)}")
    return data
