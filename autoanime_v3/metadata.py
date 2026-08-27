"""External metadata lookup via bangumi (bgm.tv) and TMDB.

Two consumers share the same search primitives:
- B1: `MetadataResolverAgent` resolves an uncertain filename into a Chinese title
      (same dict contract as `OpenAIResolverAgent`), wired into the resolver's
      fallback chain.
- B2: `MetadataSearch` also feeds library enrichment (poster/synopsis/status).

Failures never raise: every network call is wrapped and returns None, so an
outage on a metadata site can never break a scan or a library operation.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import MediaFile, ParsedName
from .normalize import display_title, unique_nonempty

BANGUMI_SEARCH = "https://api.bgm.tv/search/subject/{query}?type=2&responseGroup=small"
TMDB_TV_SEARCH = "https://api.themoviedb.org/3/search/tv"
TMDB_MOVIE_SEARCH = "https://api.themoviedb.org/3/search/movie"
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"
USER_AGENT = "autoanime-webui/1.0"

# TMDB 的 Animation(动画)genre id,用于把动画番剧与真人电视剧区分开。
ANIME_GENRE_ID = 16

# 多候选探测成本边界:最多探测的候选名数量、最多收集的外部命中数量。
# 探测池 = AI 语言候选(优先)+ 机器候选,cap 放宽到 6 让 5 个语言候选都能被查。
MAX_CANDIDATE_PROBES = 6
MAX_HITS = 3

GetJson = Callable[[str, Optional[Dict[str, str]], float], Optional[dict]]


def _get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 12.0) -> Optional[dict]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None


class MetadataSearch:
    """bangumi → TMDB 顺序搜索；任一站点失败都静默进入下一个。"""

    def __init__(
        self,
        bangumi_enabled: bool,
        tmdb_enabled: bool,
        tmdb_api_key: str = "",
        timeout: float = 12.0,
        get: Optional[GetJson] = None,
    ) -> None:
        self.bangumi_enabled = bool(bangumi_enabled)
        self.tmdb_enabled = bool(tmdb_enabled and tmdb_api_key)
        self.tmdb_api_key = tmdb_api_key
        self.timeout = max(2.0, float(timeout))
        self._get = get or _get_json

    def search(self, title: str, movie: bool = False) -> Optional[dict]:
        """Return the first matching subject dict or None. Never raises."""
        try:
            if self.bangumi_enabled:
                hit = self._search_bangumi(title)
                if hit:
                    return hit
            if self.tmdb_enabled:
                hit = self._search_tmdb(title, movie)
                if hit:
                    return hit
        except Exception:
            return None
        return None

    def _search_bangumi(self, title: str) -> Optional[dict]:
        url = BANGUMI_SEARCH.format(query=urllib.parse.quote(title))
        payload = self._get(url, {"User-Agent": USER_AGENT}, self.timeout)
        items = (payload or {}).get("list") or []
        if not items:
            return None
        item = items[0]
        name = str(item.get("name_cn") or item.get("name") or "").strip()
        if not name:
            return None
        images = item.get("images") or {}
        poster = images.get("common") or images.get("large") or images.get("medium") or ""
        air_date = str(item.get("air_date") or "").strip()
        return {
            "provider": "bgm",
            "provider_id": str(item.get("id", "")),
            "name": name,
            "poster_url": poster,
            "synopsis": str(item.get("summary") or "").strip(),
            "broadcast_status": air_date[:10] or "未知",
            "confidence": 0.9 if any("一" <= ch <= "鿿" for ch in name) else 0.8,
            "is_anime": True,  # bgm type=2 即动画
        }

    def _pick_tmdb_result(self, results: list) -> dict:
        """TMDB 检索优先选动画番剧,避免选中同名真人电视剧。

        排名:① genre_ids 含 16(Animation) → ② 日文/日本来源(original_language==ja 或 origin_country 含 JP)
        → ③ 第一条。个别条目 genre 数据缺失时回落日源/第一条。
        """
        anime = [item for item in results if ANIME_GENRE_ID in (item.get("genre_ids") or [])]
        if anime:
            return anime[0]
        japanese = [
            item
            for item in results
            if item.get("original_language") == "ja"
            or "JP" in (item.get("origin_country") or [])
        ]
        return japanese[0] if japanese else results[0]

    def _search_tmdb(self, title: str, movie: bool) -> Optional[dict]:
        endpoint = TMDB_MOVIE_SEARCH if movie else TMDB_TV_SEARCH
        url = "{0}?api_key={1}&language=zh-CN&include_adult=false&query={2}".format(
            endpoint,
            urllib.parse.quote(self.tmdb_api_key),
            urllib.parse.quote(title),
        )
        payload = self._get(url, {}, self.timeout)
        results = (payload or {}).get("results") or []
        if not results:
            return None
        item = self._pick_tmdb_result(results)
        if movie:
            name = str(item.get("title") or item.get("original_title") or "").strip()
            air_field = item.get("release_date")
        else:
            name = str(item.get("name") or item.get("original_name") or "").strip()
            air_field = item.get("first_air_date")
        if not name:
            return None
        poster = (TMDB_IMAGE + item["poster_path"]) if item.get("poster_path") else ""
        air_date = str(air_field or "").strip()
        return {
            "provider": "tmdb",
            "provider_id": str(item.get("id", "")),
            "name": name,
            "poster_url": poster,
            "synopsis": str(item.get("overview") or "").strip(),
            "broadcast_status": air_date[:10] or "未知",
            "confidence": 0.9 if any("一" <= ch <= "鿿" for ch in name) else 0.8,
            "is_anime": ANIME_GENRE_ID in (item.get("genre_ids") or []),
        }


class MetadataResolverAgent:
    """与 OpenAIResolverAgent 同契约：resolve() 返回 dict 或 None，绝不抛异常。"""

    name = "metadata"

    def __init__(self, config, get: Optional[GetJson] = None) -> None:
        self.config = config
        self.search = MetadataSearch(
            config.metadata_bangumi_enabled,
            config.metadata_tmdb_enabled,
            config.metadata_tmdb_api_key,
            config.metadata_timeout,
            get=get,
        )

    def enabled(self) -> bool:
        return bool(self.config.metadata_bangumi_enabled or self.config.metadata_tmdb_enabled)

    def resolve(self, media: MediaFile, parsed: ParsedName) -> Optional[Dict[str, Any]]:
        best, _hits = self.resolve_all(media, parsed)
        return best

    def resolve_all(self, media: MediaFile, parsed: ParsedName) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """多候选探测：遍历 title_candidates 逐个查 bgm/tmdb，收集去重后的命中。

        返回 ``(best_hit_dict, hits)``：
        - ``best_hit_dict`` 是置信度最高的命中（与旧 ``resolve`` 同契约的 dict），
          没有命中时为 ``None``；
        - ``hits`` 是给复核代理用的全部外部命中列表（provider/name/confidence/provider_id）。

        绝不抛异常：``search.search`` 自身吞掉网络错误，这里最多返回空列表。
        """
        if not self.enabled():
            return None, []
        ai_names = [
            str(name).strip()
            for (_lang, name) in (parsed.ai_candidates or [])
            if str(name).strip()
        ]
        machine_candidates = [
            candidate for candidate in (parsed.title_candidates or []) if str(candidate).strip()
        ]
        if not machine_candidates and parsed.raw_title:
            machine_candidates = [parsed.raw_title]
        # AI 语言候选优先,再补机器候选,去重保序
        candidates = unique_nonempty(ai_names + machine_candidates)
        hits: List[Dict[str, Any]] = []
        seen: set = set()
        for candidate in candidates[:MAX_CANDIDATE_PROBES]:
            if len(hits) >= MAX_HITS:
                break
            try:
                hit = self.search.search(str(candidate).strip(), movie=bool(parsed.is_movie))
            except Exception:
                hit = None
            if not hit:
                continue
            key = (hit["provider"], hit["provider_id"])
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)
        if not hits:
            return None, []
        # 优先动漫命中(is_anime),再比置信度,避免真人版置信度高反而胜出
        best = min(hits, key=lambda hit: (0 if hit.get("is_anime", True) else 1, -float(hit["confidence"])))
        return self._hit_to_dict(best, parsed), hits

    def _hit_to_dict(self, hit: Dict[str, Any], parsed: ParsedName) -> Dict[str, Any]:
        reason = (
            "{0}:subject={1}".format(hit["provider"], hit["provider_id"])
            if hit["provider_id"]
            else hit["provider"]
        )
        return {
            "title": display_title(hit["name"]),
            "season": parsed.season,
            "episode": parsed.episode,
            "is_movie": bool(parsed.is_movie),
            "confidence": max(0.0, min(1.0, float(hit["confidence"]))),
            "reason": reason,
            "provider": hit["provider"],
        }
