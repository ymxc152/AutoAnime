"""Bangumi 时间表 / 季度浏览网关（P1-C）：api.bgm.tv 只读外呼的唯一出口。

边界（对齐 gateway/rss.py）：本模块是选番数据（每日时间表、按季浏览）
的唯一网络出口。httpx AsyncClient ``trust_env=True``（复用系统代理——
bgm.tv 部分地区直连不稳，与 RSS 走 Mikan 的对策一致）；UA 必须可识别
（Bangumi 官方要求自定义 UA，复用 providers.bangumi.USER_AGENT）。

进程内缓存（内存 dict，不进 DB）：calendar 6h、season-browse 30min、
负缓存 10min（失败/降级结果也缓存，避免 bgm 被打挂时每请求都外呼）。
QPS 频控：简单间隔节流（clock/sleeper 可注入，离线测试零等待）。

失败语义：
- ``fetch_calendar`` 失败 → ``BangumiFetchError``（文案不含完整 URL，
  只有 host 与摘要，对齐 RssFetchError）；
- ``fetch_season`` 永不抛异常，降级链（1 带 air_date 过滤 → 2 仅 type
  翻页 + 客户端按条目 ``date`` 过滤 → 3 降级空结果）全部失败时返回
  ``{items: (), degraded: True, reason}``，不把 500 抛给用户。

季月区间：winter=01-01~03-31、spring=04-01~06-30、summer=07-01~09-30、
fall=10-01~12-31（Bangumi 的「季」按开播月份三分，与国内卫视口径一致）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from autoanime.providers.bangumi import ANIME_SUBJECT_TYPE, BANGUMI_BASE_URL, USER_AGENT

logger = logging.getLogger(__name__)

MIKAN_SEARCH_URL_TEMPLATE = "https://mikanani.me/Home/Search?searchstr={query}"
"""按标题跳 Mikan 搜索的外链模板（urlencode 后拼接）。"""

CALENDAR_TTL_S = 6 * 3600.0
"""每日时间表缓存 TTL（半天内重复访问不外呼）。"""

SEASON_TTL_S = 30 * 60.0
"""季度浏览缓存 TTL（放送中数据约每半小时新鲜即可）。"""

NEGATIVE_TTL_S = 10 * 60.0
"""负缓存 TTL：失败/降级结果的缓存时长（防雪崩重复外呼）。"""

SEASON_PAGE_LIMIT = 50
"""降级链第 2 档翻页每页条数（v0 search 的 limit）。"""

SUBJECT_TTL_S = 24 * 3600.0
"""subject 详情（别名提取用）缓存 TTL：infobox 别名近乎不变，缓存一天。"""

MAX_SEASON_PAGES = 100
"""翻页上限（5000 条为界，防全库翻页拖死；超限返回已过滤的部分结果）。"""

SEASON_MONTH_RANGES: dict[str, tuple[str, str]] = {
    "winter": ("01-01", "03-31"),
    "spring": ("04-01", "06-30"),
    "summer": ("07-01", "09-30"),
    "fall": ("10-01", "12-31"),
}
"""季名 → 当年的起止月日（拼在年份后面构成闭区间日期串）。"""


class BangumiFetchError(Exception):
    """拉取或解析失败；文本不含完整 URL/密钥（对齐 RssFetchError）。"""

    def __init__(self, host: str, detail: str) -> None:
        super().__init__(f"bangumi fetch failed ({host}): {detail}")
        self.host = host
        self.detail = detail


@dataclass(frozen=True)
class BangumiItem:
    """规范化后的番剧条目（时间表/季浏览共用）。

    platform = Bangumi 原样（TV/OVA/ONA/剧场版…，可空）；region 由 tags 里的
    地区标签推导（jp/cn/kr/us，可空）——前端据此做地区/特别篇过滤。
    """

    subject_id: int
    title_cn: str | None
    title_jp: str
    image_url: str | None
    rating: float | None
    air_date: str | None
    eps: int | None
    mikan_search_url: str
    platform: str | None
    region: str | None


@dataclass(frozen=True)
class SeasonBrowseResult:
    """季浏览结果；``degraded=True`` 时 ``reason`` 说明降级档位。"""

    items: tuple[BangumiItem, ...]
    degraded: bool
    reason: str | None


def mikan_search_url(title: str) -> str:
    """标题 → Mikan 搜索外链（纯函数，供测试）。"""
    return MIKAN_SEARCH_URL_TEMPLATE.format(query=quote(title))

#: tags 里的地区标签名 → 规范 region 码（Bangumi 惯例：地区作为高票 tag 存在）
_REGION_TAG_NAMES: tuple[tuple[str, str], ...] = (
    ("日本", "jp"),
    ("中国", "cn"),
    ("国产", "cn"),
    ("韩国", "kr"),
    ("美国", "us"),
    ("北美", "us"),
)


def derive_region(tags: object) -> str | None:
    """subject.tags([{name,count}…] 或 [str…]) → 地区码；无地区标签返回 None。

    Bangumi tags 按票数降序，首个命中的地区标签即认定（纯函数，供测试）。
    """
    if not isinstance(tags, list):
        return None
    for tag in tags:
        name = tag.get("name") if isinstance(tag, dict) else tag
        if not isinstance(name, str):
            continue
        for key, code in _REGION_TAG_NAMES:
            if key in name:
                return code
    return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _subject_image_url(raw: dict[str, Any]) -> str | None:
    images = raw.get("images")
    if not isinstance(images, dict):
        return None
    for key in ("common", "large", "medium"):
        value = images.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def _subject_rating(raw: dict[str, Any]) -> float | None:
    rating = raw.get("rating")
    if not isinstance(rating, dict):
        return None
    score = rating.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    return float(score) if score > 0 else None


def _subject_air_date(raw: dict[str, Any]) -> str | None:
    """v0 条目的开播日期：calendar 有 ``air_date``，search 条目叫 ``date``。"""
    for key in ("air_date", "date"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def map_subject(raw: object) -> BangumiItem | None:
    """单个 subject 字典 → 规范条目；缺 id/name 等必要字段返回 None（跳过）。"""
    if not isinstance(raw, dict):
        return None
    subject_id = _positive_int(raw.get("id"))
    title_jp = str(raw.get("name") or "").strip()
    if subject_id is None or not title_jp:
        return None
    title_cn_raw = str(raw.get("name_cn") or "").strip()
    search_title = title_cn_raw or title_jp
    platform_raw = str(raw.get("platform") or "").strip()
    return BangumiItem(
        subject_id=subject_id,
        title_cn=title_cn_raw or None,
        title_jp=title_jp,
        image_url=_subject_image_url(raw),
        rating=_subject_rating(raw),
        air_date=_subject_air_date(raw),
        eps=_positive_int(raw.get("eps")),
        mikan_search_url=mikan_search_url(search_title),
        platform=platform_raw or None,
        region=derive_region(raw.get("tags")),
    )


def map_calendar_response(payload: object) -> tuple[BangumiItem, ...]:
    """``GET /v0/calendar`` 响应 → 规范条目。

    v0 calendar 是按星期分桶的 ``[{weekday: {...}, items: [subject…]}]``；
    只展平 ``items``，坏桶/坏条目跳过。顶层非 list 视为映射失败。
    """
    if not isinstance(payload, list):
        raise ValueError("unexpected calendar payload shape (not a list)")
    items: list[BangumiItem] = []
    for bucket in payload:
        if not isinstance(bucket, dict):
            continue
        for raw in bucket.get("items") or []:
            item = map_subject(raw)
            if item is not None:
                items.append(item)
    return tuple(items)


# --- subject 别名提取（别名富化：跨语言标题命中 expected_titles） ----------

#: str 形式的 infobox value 的分隔符（Bangumi 惯例：逗号/顿号/斜杠/分号混用）
_ALIAS_SEPARATORS_RE = re.compile(r"[,，/、;；]")


def extract_subject_aliases(payload: object) -> tuple[str, ...]:
    """``GET /v0/subjects/{id}`` 响应 → 别名元组（纯函数，供测试）。

    从 infobox 提取「别名」条的各值：``value`` 可能是 ``str``（逗号/顿号/
    斜杠/分号分隔）、``list[str]`` 或 ``list[{v: str}]``。去重（casefold
    归一比较、保留首个原样）、剔除与主名（``name``/``name_cn``）相同的
    项。payload 不是 dict 或 infobox 缺失/无「别名」条 → 空元组（subject
    本来就可能没填别名，不算失败）。
    """
    if not isinstance(payload, dict):
        return ()
    mains = {
        str(payload.get(key) or "").strip().casefold()
        for key in ("name", "name_cn")
    }
    mains.discard("")
    aliases: list[str] = []
    seen: set[str] = set()
    for entry in payload.get("infobox") or []:
        if not isinstance(entry, dict) or entry.get("key") != "别名":
            continue
        value = entry.get("value")
        raw_values: list[str] = []
        if isinstance(value, str):
            raw_values = [value]
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    raw_values.append(item)
                elif isinstance(item, dict) and isinstance(item.get("v"), str):
                    raw_values.append(item["v"])
        for raw in raw_values:
            for piece in _ALIAS_SEPARATORS_RE.split(raw):
                alias = piece.strip()
                if not alias:
                    continue
                folded = alias.casefold()
                if folded in seen or folded in mains:
                    continue
                seen.add(folded)
                aliases.append(alias)
    return tuple(aliases)


async def fetch_subject_aliases(
    client: httpx.AsyncClient, subject_id: int
) -> tuple[str, ...]:
    """拉 ``GET /v0/subjects/{id}`` 并提取 infobox「别名」（轻量网络出口）。

    与 ``BangumiCalendarGateway._request_json`` 同失败语义：网络/HTTP/
    JSON/载荷形状失败抛 ``BangumiFetchError``（文本只含 host 与摘要）。
    别名缺失返回空元组。请求带 Bangumi 官方要求的可识别 UA。
    """
    host = httpx.URL(BANGUMI_BASE_URL).host or "api.bgm.tv"
    try:
        response = await client.get(
            f"{BANGUMI_BASE_URL}/v0/subjects/{subject_id}",
            headers={"User-Agent": USER_AGENT},
        )
    except httpx.HTTPError as exc:
        raise BangumiFetchError(host, type(exc).__name__) from None
    if not (200 <= response.status_code < 300):
        raise BangumiFetchError(host, f"http {response.status_code}")
    try:
        payload = response.json()
    except (ValueError, TypeError):
        raise BangumiFetchError(host, "invalid json") from None
    if not isinstance(payload, dict):
        raise BangumiFetchError(host, "unexpected subject payload shape")
    return extract_subject_aliases(payload)


def _in_month_range(date_str: str | None, start: str, end: str) -> bool:
    """条目日期（``YYYY-MM-DD`` 前缀比对）是否落在闭区间内。"""
    if not date_str:
        return False
    day = date_str[:10]
    return start <= day <= end


def _filter_by_month(payload: list[Any], start: str, end: str) -> tuple[BangumiItem, ...]:
    """映射 + 按月区间过滤（search 条目按自带 ``date``/``air_date``）。"""
    items: list[BangumiItem] = []
    for raw in payload:
        item = map_subject(raw)
        if item is not None and _in_month_range(item.air_date, start, end):
            items.append(item)
    return tuple(items)


class BangumiCalendarGateway:
    """时间表/季浏览网关：频控 + TTL 缓存 + 降级链，实例状态（非全局）。

    ``transport`` 可注入（单测 ``httpx.MockTransport``）；``clock``/``sleeper``
    可注入（离线零等待）。真实 client ``trust_env=True`` 复用系统代理。
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        timeout_s: float = 10.0,
        qps: float = 2.0,
        base_url: str = BANGUMI_BASE_URL,
    ) -> None:
        self._transport = transport
        self._clock = clock if clock is not None else time.monotonic
        self._sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._timeout = httpx.Timeout(timeout_s)
        self._min_interval_s = 1.0 / qps if qps > 0 else 0.0
        self._base_url = base_url.rstrip("/")
        self._last_request_at: float | None = None
        self._client: httpx.AsyncClient | None = None
        # TTL 缓存：key → (expires_at, value)；value 可为条目元组/降级结果/
        # BangumiFetchError（负缓存 calendar，命中时原样重抛）。
        self._cache: dict[Any, tuple[float, object]] = {}

    async def aclose(self) -> None:
        """释放底层 client（lifespan shutdown 时调用）。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- HTTP 层 -------------------------------------------------------------

    async def _throttle(self) -> None:
        now = self._clock()
        if (
            self._min_interval_s > 0.0
            and self._last_request_at is not None
        ):
            wait = self._min_interval_s - (now - self._last_request_at)
            if wait > 0.0:
                await self._sleeper(wait)
        self._last_request_at = self._clock()

    async def _request_json(self, method: str, path: str, *, json_body: object = None) -> object:
        """发一次请求并解析 JSON；网络/HTTP/解析失败抛 ``BangumiFetchError``。"""
        host = httpx.URL(self._base_url).host or "api.bgm.tv"
        await self._throttle()
        try:
            client = await self._ensure_client()
            response = await client.request(method, f"{self._base_url}{path}", json=json_body)
        except httpx.HTTPError as exc:
            raise BangumiFetchError(host, type(exc).__name__) from None
        if not (200 <= response.status_code < 300):
            raise BangumiFetchError(host, f"http {response.status_code}")
        try:
            return response.json()
        except (ValueError, TypeError):
            raise BangumiFetchError(host, "invalid json") from None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                trust_env=True,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    # --- 缓存层 --------------------------------------------------------------

    def _cache_get(self, key: Any) -> object | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            self._cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key: Any, value: object, ttl_s: float) -> None:
        self._cache[key] = (self._clock() + ttl_s, value)

    # --- 对外入口 ------------------------------------------------------------

    async def fetch_calendar(self) -> tuple[BangumiItem, ...]:
        """每日时间表（缓存 6h）；失败负缓存 10min 并抛 ``BangumiFetchError``。"""
        cached = self._cache_get("calendar")
        if isinstance(cached, BangumiFetchError):
            raise cached
        if isinstance(cached, tuple):
            return cached
        try:
            # 注意：日历端点是 ``GET /calendar``（无 /v0 前缀，实测 /v0/calendar 404）。
            payload = await self._request_json("GET", "/calendar")
            items = map_calendar_response(payload)
        except (BangumiFetchError, ValueError) as exc:
            detail = exc.detail if isinstance(exc, BangumiFetchError) else f"map: {exc}"
            error = BangumiFetchError("api.bgm.tv", detail)
            self._cache_set("calendar", error, NEGATIVE_TTL_S)
            raise error from None
        self._cache_set("calendar", items, CALENDAR_TTL_S)
        return items

    async def fetch_season(self, year: int, season: str) -> SeasonBrowseResult:
        """按季浏览（缓存 30min，降级结果负缓存 10min）；永不抛异常。"""
        key = ("season", year, season)
        cached = self._cache_get(key)
        if isinstance(cached, SeasonBrowseResult):
            return cached
        result = await self._fetch_season_inner(year, season)
        self._cache_set(key, result, NEGATIVE_TTL_S if result.degraded else SEASON_TTL_S)
        return result

    async def fetch_subject_aliases(self, subject_id: int) -> tuple[str, ...]:
        """subject 别名（缓存 24h、失败负缓存 10min）；复用网关 client 与频控。

        订阅创建路径（P0-B）用：拉 ``/v0/subjects/{id}`` 提取 infobox
        「别名」。失败语义同 ``fetch_calendar``：抛 ``BangumiFetchError``
        （负缓存防 bgm 故障时订阅创建路径反复外呼）。
        """
        key = ("subject-aliases", subject_id)
        cached = self._cache_get(key)
        if isinstance(cached, BangumiFetchError):
            raise cached
        if isinstance(cached, tuple):
            return cached
        await self._throttle()
        try:
            client = await self._ensure_client()
            aliases = await fetch_subject_aliases(client, subject_id)
        except BangumiFetchError as exc:
            self._cache_set(key, exc, NEGATIVE_TTL_S)
            raise
        self._cache_set(key, aliases, SUBJECT_TTL_S)
        return aliases

    # --- 降级链 ---------------------------------------------------------------

    async def _fetch_season_inner(self, year: int, season: str) -> SeasonBrowseResult:
        start = f"{year:04d}-{SEASON_MONTH_RANGES[season][0]}"
        end = f"{year:04d}-{SEASON_MONTH_RANGES[season][1]}"
        last_error = "all attempts failed"
        # 档 1：search 端点带 air_date 区间过滤（v0 要求数组形式——
        # 字符串形式实测 400 "expected=[]string"；数组形式 2026-09 实测可用）。
        try:
            payload = await self._request_json(
                "POST",
                "/v0/search/subjects",
                json_body={
                    "keywords": "",
                    "filter": {
                        "type": [ANIME_SUBJECT_TYPE],
                        "air_date": [f">={start}", f"<={end}"],
                    },
                },
            )
        except BangumiFetchError as exc:
            last_error = exc.detail
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            items = _filter_by_month(payload["data"], start, end)
            return SeasonBrowseResult(items=items, degraded=False, reason=None)
        # 档 2：仅 type 过滤翻页拉回，按条目自带 date 过滤月区间。
        collected: list[BangumiItem] = []
        offset = 0
        for _ in range(MAX_SEASON_PAGES):
            try:
                page = await self._request_json(
                    "POST",
                    "/v0/search/subjects",
                    json_body={
                        "keywords": "",
                        "filter": {"type": [ANIME_SUBJECT_TYPE]},
                        "limit": SEASON_PAGE_LIMIT,
                        "offset": offset,
                    },
                )
            except BangumiFetchError as exc:
                last_error = exc.detail
                page = None
            if not (isinstance(page, dict) and isinstance(page.get("data"), list)):
                break  # 翻页中途失败 → 档 3
            batch = page["data"]
            collected.extend(_filter_by_month(batch, start, end))
            if len(batch) < SEASON_PAGE_LIMIT:
                return SeasonBrowseResult(items=tuple(collected), degraded=False, reason=None)
            offset += len(batch)
        else:
            # 翻页达上限：已拉回的部分仍是有效数据（可能不全，如实返回）。
            logger.warning("season browse pagination hit page cap (%s)", MAX_SEASON_PAGES)
            return SeasonBrowseResult(items=tuple(collected), degraded=False, reason=None)
        # 档 3：全链失败 → 降级空结果，不抛 500。
        return SeasonBrowseResult(items=(), degraded=True, reason=last_error)
