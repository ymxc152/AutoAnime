"""Bangumi（api.bgm.tv v0）MetadataReference 参考源适配器。

权威参考（验证/消歧/规范名），不是识别源。查询粒度是剧目级：
入参是 L2 title shape（casefold、``{season}``/``{ep}`` 占位符化），先经
``bare_query`` 折叠成检索关键词，``POST /v0/search/subjects``（动画
``subject_type=2``）取候选，``pick_candidate`` 选出命中条目，再
``GET /v0/subjects/{id}`` 取权威详情映射为 ``ReferenceFacts``。

候选匹配范围（PR7 M1）：每个检索条目的 ``name``/``name_cn`` 加上该条目
自带 infobox 的「别名」「中文名」（v0 搜索响应条目自带 infobox，罗马音
query 靠别名精确命中）；检索结果列表内全部条目共同参与匹配，最终 facts
仍取最相似命中条目的详情。

字段映射（API → ReferenceFacts）：
- canonical_title = ``name_cn``（中文名优先），空则 ``name``；
- aliases = ``name`` + infobox「别名」各值 + infobox「中文名」（去重、
  排除 canonical 本身）；
- episode_count = ``eps`` > infobox「话数」 > ``total_episodes``（取第一个
  正值；录制样本中 base 条目 eps=28 而 total_episodes=36，后者聚合了
  相关内容，故 eps/话数 优先）；
- seasons = 本条目季号 ∪ 检索结果中同名兄弟条目季号（Bangumi 以单季为
  条目粒度，一次检索通常带回同作品各季；季号由标题标记推导，无标记
  记为 1）——多季元组供 R6 消歧。

失败语义：网络错误/超时/4xx/5xx/非 JSON/查无结果/解析失败 → 返回
``None``（链继续问下一个 provider），绝不抛异常到管线。429 退避由共用
``ReferenceHttpClient`` 处理。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from autoanime.pipeline.l3.reference import ReferenceFacts
from autoanime.providers._reference_http import (
    DEFAULT_QPS,
    DEFAULT_TIMEOUT_S,
    ReferenceHttpClient,
    bare_query,
    normalize_title,
    pick_candidate,
)

BANGUMI_BASE_URL = "https://api.bgm.tv"
"""Bangumi v0 API 根地址（测试可注入替换）。"""

ANIME_SUBJECT_TYPE = 2
"""Bangumi subject type=2 是动画。"""

USER_AGENT = "autoanime/2.0.0.dev0 (local-first anime library automation)"
"""Bangumi 要求可识别的自定义 User-Agent。"""

# 季号推导：第N季/第N期（含中文数字）、Season N、SN、罗马数字。
_SEASON_CN_RE = re.compile(r"第\s*([0-9一二三四五六七八九十]+)\s*[季期]")
_SEASON_EN_RE = re.compile(r"\bseason\s*(\d{1,2})\b", re.IGNORECASE)
_SEASON_S_RE = re.compile(r"\bs(\d{1,2})\b", re.IGNORECASE)
_SEASON_ROMAN_RE = re.compile(r"\b(II|III|IV)\b")
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_ROMAN_DIGITS = {"II": 2, "III": 3, "IV": 4}
_SEASON_MARKER_RE = re.compile(
    r"第\s*[0-9一二三四五六七八九十]+\s*[季期]|\bseason\s*\d{1,2}\b|\bs\d{1,2}\b|\b(?:II|III|IV)\b",
    re.IGNORECASE,
)


def _cn_number_to_int(text: str) -> int | None:
    """中文数字转 int（支持 1-19 的常用形式），失败返回 None。"""
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        tens, _, ones = text.partition("十")
        if tens:
            tens_digit = _CN_DIGITS.get(tens)
            if tens_digit is None:
                return None
            value = tens_digit * 10
        else:
            # 「十一」= 10 + 1（十位缺省时「十」本身为 10）。
            value = 10
        if ones:
            ones_digit = _CN_DIGITS.get(ones)
            if ones_digit is None:
                return None
            value += ones_digit
        return value
    return _CN_DIGITS.get(text)


def detect_season_number(*titles: str) -> int | None:
    """从标题标记推导季号；无标记返回 None。纯函数。"""
    for title in titles:
        if not title:
            continue
        if match := _SEASON_CN_RE.search(title):
            number = _cn_number_to_int(match.group(1))
            if number:
                return number
        if match := _SEASON_EN_RE.search(title):
            return int(match.group(1))
        if match := _SEASON_S_RE.search(title):
            return int(match.group(1))
        if match := _SEASON_ROMAN_RE.search(title):
            return _ROMAN_DIGITS[match.group(1)]
    return None


def strip_season_markers(title: str) -> str:
    """去掉季标记后的标题（用于兄弟条目的 bare-name 对齐）。纯函数。"""
    return _SEASON_MARKER_RE.sub(" ", title)


def _infobox_values(detail: dict[str, Any], key: str) -> list[str]:
    """取 infobox 指定 key 的字符串值（value 可能是 str / list[str] / list[{v}]）。"""
    for entry in detail.get("infobox") or []:
        if not isinstance(entry, dict) or entry.get("key") != key:
            continue
        value = entry.get("value")
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            results: list[str] = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    results.append(item.strip())
                elif isinstance(item, dict) and isinstance(item.get("v"), str) and item["v"].strip():
                    results.append(item["v"].strip())
            return results
        return []
    return []


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _candidate_names(item: dict[str, Any]) -> tuple[str, ...]:
    """检索条目 → 参与匹配的名字集合（去空、去重、保序）。

    范围 = ``name`` + ``name_cn`` + 该条目自带 infobox 的「别名」「中文名」
    各值。Bangumi v0 搜索响应的条目自带 infobox（真实响应已验证），罗马音
    query（如 "Sousou no Frieren"）正是靠条目自带的别名完成精确匹配——
    仅比 name/name_cn 无法覆盖罗马音。
    """
    names = [str(item.get("name") or ""), str(item.get("name_cn") or "")]
    names.extend(_infobox_values(item, "别名"))
    names.extend(_infobox_values(item, "中文名"))
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            unique.append(name)
    return tuple(unique)


class BangumiReference:
    """Bangumi 参考源插件（Registry 注册名 ``"bangumi"``）。

    transport/clock/sleeper 可注入（离线测试）；频控状态在共用
    ``ReferenceHttpClient`` 的实例上。
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        qps: float = DEFAULT_QPS,
        base_url: str = BANGUMI_BASE_URL,
        client: ReferenceHttpClient | None = None,
    ) -> None:
        self._http = client if client is not None else ReferenceHttpClient(
            transport=transport,
            clock=clock,
            sleeper=sleeper,
            timeout_s=timeout_s,
            qps=qps,
            headers={"User-Agent": USER_AGENT},
        )
        self._base_url = base_url.rstrip("/")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        """MetadataReference 契约入口：任何失败路径都返回 ``None``。"""
        try:
            return await self._lookup(title_shape)
        except Exception:
            # 兜底：解析/映射中的意外异常也不进管线（异常不外传，不回显 URL）。
            return None

    async def _lookup(self, title_shape: str) -> ReferenceFacts | None:
        query = bare_query(title_shape)
        if not query:
            return None
        search = await self._http.request_json(
            "POST",
            f"{self._base_url}/v0/search/subjects",
            json_body={"keyword": query, "filter": {"type": [ANIME_SUBJECT_TYPE]}},
        )
        if not isinstance(search, dict):
            return None
        hits = [item for item in search.get("data") or [] if isinstance(item, dict) and item.get("id")]
        if not hits:
            return None
        candidates = [(int(item["id"]), _candidate_names(item)) for item in hits]
        chosen = pick_candidate(candidates, query)
        if chosen is None:
            return None
        detail = await self._http.request_json("GET", f"{self._base_url}/v0/subjects/{chosen}")
        if not isinstance(detail, dict):
            return None
        return _map_subject(detail, sibling_hits=hits)


def _map_subject(
    detail: dict[str, Any], *, sibling_hits: list[dict[str, Any]]
) -> ReferenceFacts:
    """subject 结构 → ReferenceFacts（推导规则见模块 docstring）。"""
    name = str(detail.get("name") or "")
    name_cn = str(detail.get("name_cn") or "")
    canonical = name_cn or name or None

    aliases: list[str] = []
    for alias in (name, *_infobox_values(detail, "别名"), *_infobox_values(detail, "中文名")):
        normalized = normalize_title(alias)
        if not normalized or not alias:
            continue
        if canonical is not None and normalized == normalize_title(canonical):
            continue
        if alias not in aliases:
            aliases.append(alias)

    # episode_count：eps > 话数 > total_episodes，取第一个正值。
    episode_count = _positive_int(detail.get("eps"))
    if episode_count is None:
        for value in _infobox_values(detail, "话数"):
            episode_count = _positive_int(value)
            if episode_count is not None:
                break
    if episode_count is None:
        episode_count = _positive_int(detail.get("total_episodes"))

    seasons = _derive_seasons(detail, sibling_hits)
    return ReferenceFacts(
        canonical_title=canonical,
        seasons=seasons,
        episode_count=episode_count,
        aliases=tuple(aliases),
        source="bangumi",
        poster_url=_subject_poster_url(detail),
    )


def _derive_seasons(
    detail: dict[str, Any], sibling_hits: list[dict[str, Any]]
) -> tuple[int, ...]:
    """季列表推导：本条目季号 + 检索结果中同名（去季标记后）兄弟条目季号。"""
    name = str(detail.get("name") or "")
    name_cn = str(detail.get("name_cn") or "")
    own = detect_season_number(name, name_cn) or 1
    seasons = {own}
    own_bare = {normalize_title(strip_season_markers(name)), normalize_title(strip_season_markers(name_cn))}
    own_bare.discard("")
    for hit in sibling_hits:
        hit_name = str(hit.get("name") or "")
        hit_name_cn = str(hit.get("name_cn") or "")
        hit_bare = {
            normalize_title(strip_season_markers(hit_name)),
            normalize_title(strip_season_markers(hit_name_cn)),
        }
        hit_bare.discard("")
        if not hit_bare or not (hit_bare & own_bare):
            continue
        number = detect_season_number(hit_name, hit_name_cn)
        seasons.add(number or 1)
    return tuple(sorted(seasons))


def _subject_poster_url(detail: dict[str, Any]) -> str | None:
    """subject 详情 → 海报直链：``images.large`` → ``common`` → ``medium``。

    Bangumi v0 subject 详情自带 ``images`` 对象；取首个 http(s) 直链，
    缺失/形状不符返回 ``None``（海报是增强信息，缺失不影响 facts 有效性）。
    """
    images = detail.get("images")
    if not isinstance(images, dict):
        return None
    for key in ("large", "common", "medium"):
        value = images.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None
