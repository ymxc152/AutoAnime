"""Mikan 字幕组 RSS 发现网关（选番抽屉「自动获取 RSS」数据源）。

边界（铁律 3）：本模块是 Mikan 发现类请求的唯一网络出口（RSS 拉取仍在
gateway/rss.py）。Mikan 无公开发现 API（/api/v2/* 实测 404），走经典 HTML
页：先 /Home/Search?searchstr= 定位番剧（首个命中 = 最佳匹配），再
/Home/Bangumi/{id} 抓「subgroup-text」块的 字幕组名 + 公开 RSS 链接。
httpx trust_env 复用 HTTPS_PROXY（与 rss.py 同一被墙对策）。

密钥纪律：字幕组 RSS 为公开地址（无 token）；若未来出现带 token 的地址，
同样只透传不记日志。失败语义：网络/非 200/页面无内容 → ``MikanLookupError``，
由路由层转 502；搜索无命中 → ``MikanLookupMiss`` 转 404，不 crash。
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://mikanani.me"
_SEARCH_URL = f"{_BASE}/Home/Search"
_BANGUMI_URL_TEMPLATE = f"{_BASE}/Home/Bangumi/{{bangumi_id}}"

_USER_AGENT = "AutoAnime/2.0 (+https://github.com/autoanime) local-first rss reader"

# 搜索结果项:href="/Home/Bangumi/{id}" 的 <a> 内有 class="an-text" 的标题节点
_SEARCH_ITEM_RE = re.compile(
    r'href="/Home/Bangumi/(?P<id>\d+)"[^>]*>.*?class="an-text"[^>]*>(?P<title>[^<]+)<',
    re.DOTALL,
)
# 字幕组块:<div class="subgroup-text" id="{subgroupid}"> 内
#   第一个 <a> = 组名(可含 HTML 实体),含 /RSS/Bangumi 的 <a> = 订阅地址
_SUBGROUP_BLOCK_RE = re.compile(
    r'<div class="subgroup-text"[^>]*>(?P<body>.*?)</div>', re.DOTALL
)
_GROUP_NAME_RE = re.compile(r"<a[^>]*>(?P<name>[^<]+)</a>")
_GROUP_RSS_RE = re.compile(r'href="(?P<url>[^"]*/RSS/Bangumi\?[^"]*subgroupid=\d+[^"]*)"')


class MikanLookupError(Exception):
    """Mikan 发现失败（网络/非 200/页面无内容）。detail 不含任何密钥。"""

    def __init__(self, host: str, detail: str) -> None:
        super().__init__(f"mikan {host}: {detail}")
        self.host = host
        self.detail = detail


class MikanLookupMiss(MikanLookupError):
    """搜索无命中（区别于网络失败，HTTP 语义应为 404）。"""


@dataclass(frozen=True)
class MikanBangumiHit:
    """搜索命中：Mikan 的 bangumi_id 与 Bangumi 的 subject_id 不同源。"""

    bangumi_id: int
    title: str


@dataclass(frozen=True)
class MikanGroupOption:
    """一个字幕组的公开 RSS 订阅地址（可直接挂 RssSource）。"""

    group_id: str
    group_name: str
    rss_url: str


def parse_search(page_html: str) -> tuple[MikanBangumiHit, ...]:
    """解析 /Home/Search 结果页;按出现序去重,无标题的条目跳过。"""
    hits: list[MikanBangumiHit] = []
    seen: set[int] = set()
    for m in _SEARCH_ITEM_RE.finditer(page_html):
        bangumi_id = int(m.group("id"))
        if bangumi_id in seen:
            continue
        title = html_lib.unescape(m.group("title")).strip()
        if title == "":
            continue
        seen.add(bangumi_id)
        hits.append(MikanBangumiHit(bangumi_id=bangumi_id, title=title))
    return tuple(hits)


def parse_groups(page_html: str) -> tuple[MikanGroupOption, ...]:
    """解析 /Home/Bangumi/{id} 页的字幕组块;按 rss_url 去重,无链跳过。"""
    options: list[MikanGroupOption] = []
    seen: set[str] = set()
    for block in _SUBGROUP_BLOCK_RE.finditer(page_html):
        body = block.group("body")
        rss_m = _GROUP_RSS_RE.search(body)
        if rss_m is None:
            continue
        rss_url = html_lib.unescape(rss_m.group("url"))
        if rss_url.startswith("/"):
            rss_url = f"{_BASE}{rss_url}"
        if rss_url in seen:
            continue
        name_m = _GROUP_NAME_RE.search(body)
        name = html_lib.unescape(name_m.group("name")).strip() if name_m else ""
        group_id = re.search(r"subgroupid=(\d+)", rss_url)
        seen.add(rss_url)
        options.append(
            MikanGroupOption(
                group_id=group_id.group(1) if group_id else "",
                group_name=name,
                rss_url=rss_url,
            )
        )
    return tuple(options)


async def _get_page(client: httpx.AsyncClient, url: str) -> str:
    host = urlsplit(url).netloc or "unknown"
    try:
        response = await client.get(url, headers={"User-Agent": _USER_AGENT})
    except httpx.HTTPError as exc:
        raise MikanLookupError(host, type(exc).__name__) from None
    if response.status_code != 200:
        raise MikanLookupError(host, f"http {response.status_code}")
    return response.text


async def search_bangumi(
    client: httpx.AsyncClient, title: str
) -> tuple[MikanBangumiHit, ...]:
    """按标题搜索 Mikan 番剧,返回候选(页面排序,首个为最佳匹配)。"""
    page = await _get_page(client, f"{_SEARCH_URL}?searchstr={quote(title, safe='')}")
    return parse_search(page)


async def fetch_subtitle_groups(
    client: httpx.AsyncClient, bangumi_id: int
) -> tuple[MikanGroupOption, ...]:
    """抓取某 Mikan 番剧页的字幕组 RSS 选项;空列表视为形状不符(防御)。"""
    page = await _get_page(client, _BANGUMI_URL_TEMPLATE.format(bangumi_id=bangumi_id))
    options = parse_groups(page)
    if not options:
        raise MikanLookupError(urlsplit(_SEARCH_URL).netloc, "no subtitle groups")
    return options


async def resolve_subtitle_groups(
    client: httpx.AsyncClient, title: str
) -> tuple[MikanBangumiHit, tuple[MikanGroupOption, ...]]:
    """一步发现:标题 → 最佳匹配番剧 → 其字幕组 RSS 选项。"""
    hits = await search_bangumi(client, title)
    if not hits:
        raise MikanLookupMiss(urlsplit(_SEARCH_URL).netloc, "no search hits")
    best = hits[0]
    options = await fetch_subtitle_groups(client, best.bangumi_id)
    return best, options
