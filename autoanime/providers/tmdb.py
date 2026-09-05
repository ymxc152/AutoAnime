"""TMDB（api.themoviedb.org/3）MetadataReference 参考源适配器。

权威参考（验证/消歧/规范名），不是识别源。查询粒度是剧目级：入参是
L2 title shape，先经 ``bare_query`` 折叠成检索关键词，
``GET /3/search/tv``（``language=zh-CN``）取候选，``pick_candidate`` 选出
命中条目，再 ``GET /3/tv/{id}`` 取权威详情映射为 ``ReferenceFacts``。

密钥：v3 ``api_key`` 走环境变量 ``AUTOANIME_TMDB_API_KEY``（构造参数可
覆盖），内部以 ``SecretStr`` 持有；未配置时 ``lookup`` 直接返回 ``None``
（链继续问下一个 provider），不发任何请求。密钥不进日志、不进异常文案
（本 adapter 不向外抛异常）。

字段映射（API → ReferenceFacts）：
- canonical_title = 详情 ``name``（zh-CN 本地化名），空则
  ``original_name``；
- aliases = ``original_name``（去重、排除 canonical）；TMDB 的别名在
  ``/tv/{id}/alternative_titles``，为控制请求数本 PR 不取——降级策略，
  后续可扩展；
- seasons = ``tuple(range(1, number_of_seasons + 1))``；
- episode_count = ``number_of_episodes``（正值才取）。

失败语义：网络错误/超时/4xx/5xx/非 JSON/查无结果/解析失败 → 返回
``None``，绝不抛异常到管线。429 退避由共用 ``ReferenceHttpClient`` 处理。
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import SecretStr

from autoanime.pipeline.l3.reference import ReferenceFacts
from autoanime.providers._reference_http import (
    DEFAULT_QPS,
    DEFAULT_TIMEOUT_S,
    ReferenceHttpClient,
    bare_query,
    pick_candidate,
)

TMDB_BASE_URL = "https://api.themoviedb.org/3"
"""TMDB v3 API 根地址（测试可注入替换）。"""

TMDB_API_KEY_ENV = "AUTOANIME_TMDB_API_KEY"
"""v3 api_key 的环境变量名。"""

DEFAULT_LANGUAGE = "zh-CN"
"""本地化语言（canonical_title 取中文名）。"""


def _resolve_api_key(api_key: SecretStr | str | None) -> SecretStr | None:
    """构造参数优先，否则读环境变量；统一包成 SecretStr（空值视为未配置）。"""
    if api_key is None:
        api_key = os.environ.get(TMDB_API_KEY_ENV)
    if api_key is None or (isinstance(api_key, str) and not api_key.strip()):
        return None
    return api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class TmdbReference:
    """TMDB 参考源插件（Registry 注册名 ``"tmdb"``）。

    transport/clock/sleeper 可注入（离线测试）；频控状态在共用
    ``ReferenceHttpClient`` 的实例上。
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        qps: float = DEFAULT_QPS,
        base_url: str = TMDB_BASE_URL,
        language: str = DEFAULT_LANGUAGE,
        client: ReferenceHttpClient | None = None,
    ) -> None:
        self._api_key = _resolve_api_key(api_key)
        self._http = client if client is not None else ReferenceHttpClient(
            transport=transport,
            clock=clock,
            sleeper=sleeper,
            timeout_s=timeout_s,
            qps=qps,
        )
        self._base_url = base_url.rstrip("/")
        self._language = language

    @property
    def configured(self) -> bool:
        """是否已配置 api_key（未配置的 provider 不发请求、直接 miss）。"""
        return self._api_key is not None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        """MetadataReference 契约入口：任何失败路径都返回 ``None``。"""
        try:
            return await self._lookup(title_shape)
        except Exception:
            # 兜底：解析/映射中的意外异常也不进管线（异常不外传，不回显 URL/key）。
            return None

    async def _lookup(self, title_shape: str) -> ReferenceFacts | None:
        if self._api_key is None:
            return None
        query = bare_query(title_shape)
        if not query:
            return None
        search = await self._http.request_json(
            "GET",
            f"{self._base_url}/search/tv",
            params={
                "api_key": self._api_key.get_secret_value(),
                "language": self._language,
                "query": query,
                "page": "1",
                "include_adult": "false",
            },
        )
        if not isinstance(search, dict):
            return None
        candidates: list[tuple[int, tuple[str, ...]]] = []
        for item in search.get("results") or []:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            candidates.append(
                (
                    int(item["id"]),
                    (str(item.get("name") or ""), str(item.get("original_name") or "")),
                )
            )
        if not candidates:
            return None
        chosen = pick_candidate(candidates, query)
        if chosen is None:
            return None
        detail = await self._http.request_json(
            "GET",
            f"{self._base_url}/tv/{chosen}",
            params={
                "api_key": self._api_key.get_secret_value(),
                "language": self._language,
            },
        )
        if not isinstance(detail, dict):
            return None
        return _map_tv_detail(detail)


def _map_tv_detail(detail: dict[str, Any]) -> ReferenceFacts:
    """tv 详情 → ReferenceFacts（映射规则见模块 docstring）。"""
    name = str(detail.get("name") or "")
    original_name = str(detail.get("original_name") or "")
    canonical = name or original_name or None

    aliases: list[str] = []
    for alias in (original_name,):
        if alias and canonical is not None and alias == canonical:
            continue
        if alias and alias not in aliases:
            aliases.append(alias)

    number_of_seasons = _positive_int(detail.get("number_of_seasons")) or 0
    seasons = tuple(range(1, number_of_seasons + 1))
    episode_count = _positive_int(detail.get("number_of_episodes"))
    return ReferenceFacts(
        canonical_title=canonical,
        seasons=seasons,
        episode_count=episode_count,
        aliases=tuple(aliases),
        source="tmdb",
    )
