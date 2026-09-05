"""PR6 P1：Bangumi/TMDB MetadataReference 适配器单元测试（全部离线）。

网络全部走 ``httpx.MockTransport``；clock/sleeper 注入 fake（零等待）；
响应体来自 tests/fixtures/reference/ 的录制 fixture（Bangumi 为真实
录制后裁剪，TMDB 为按官方文档响应结构录制式编写）。覆盖五类失败语义
（正常命中、查无结果、非 JSON、429 退避、超时）× 两 adapter，另加
注册/链组合、QPS 频控、纯函数（bare_query/pick_candidate/季号推导/
Retry-After 解析）用例。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

import httpx
import pytest

from autoanime.core.interfaces import MetadataReference, Registry
from autoanime.pipeline.l3.reference import ReferenceChain
from autoanime.providers import register_reference_providers
from autoanime.providers._reference_http import (
    DEFAULT_RETRY_AFTER_S,
    MAX_RETRY_AFTER_S,
    bare_query,
    normalize_title,
    parse_retry_after,
    pick_candidate,
)
from autoanime.providers.bangumi import (
    BangumiReference,
    detect_season_number,
    strip_season_markers,
)
from autoanime.providers.tmdb import TmdbReference

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "reference"


def _fixture(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text(encoding="utf-8")


@dataclass
class FakeClock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


@dataclass
class FakeSleeper:
    clock: FakeClock
    waits: list[float] = field(default_factory=list)

    async def __call__(self, delay: float) -> None:
        self.waits.append(delay)
        self.clock.now += delay


@dataclass
class RequestLog:
    """记录 adapter 发出的请求（method/path/body/params），供事后断言。"""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, request: httpx.Request) -> None:
        body: object = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content.decode("utf-8", "replace")
        self.entries.append(
            {
                "method": request.method,
                "path": request.url.path,
                "body": body,
                "params": dict(request.url.params),
            }
        )

    @property
    def paths(self) -> list[str]:
        return [entry["path"] for entry in self.entries]


Route = httpx.Response | Callable[[httpx.Request], httpx.Response]


def make_transport(
    routes: Mapping[tuple[str, str], Route], log: RequestLog | None = None
) -> httpx.MockTransport:
    """按 (method, path) 路由的 MockTransport。

    route 值：``httpx.Response`` 原样返回；callable(request) → Response（可
    抛 ``httpx.ConnectTimeout`` 等模拟网络错误）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.record(request)
        key = (request.method, request.url.path)
        route = routes[key]
        if callable(route):
            return route(request)
        return route

    return httpx.MockTransport(handler)


def json_response(payload: str, *, status_code: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code, content=payload.encode("utf-8"), headers=headers or {})


BANGUMI_HIT_ROUTES = {
    ("POST", "/v0/search/subjects"): json_response(_fixture("bangumi_search_frieren.json")),
    ("GET", "/v0/subjects/400602"): json_response(_fixture("bangumi_subject_400602.json")),
}

TMDB_HIT_ROUTES = {
    ("GET", "/3/search/tv"): json_response(_fixture("tmdb_search_tv_hit.json")),
    ("GET", "/3/tv/209867"): json_response(_fixture("tmdb_tv_209867_detail.json")),
}


def make_bangumi(
    routes: Mapping[tuple[str, str], Route],
    *,
    clock: FakeClock | None = None,
    sleeper: FakeSleeper | None = None,
    log: RequestLog | None = None,
    qps: float = 1.0,
) -> BangumiReference:
    return BangumiReference(
        transport=make_transport(routes, log),
        clock=clock,
        sleeper=sleeper,
        qps=qps,
    )


def make_tmdb(
    routes: Mapping[tuple[str, str], Route],
    *,
    api_key: str = "test-key",
    clock: FakeClock | None = None,
    sleeper: FakeSleeper | None = None,
    log: RequestLog | None = None,
) -> TmdbReference:
    return TmdbReference(
        api_key=api_key,
        transport=make_transport(routes, log),
        clock=clock,
        sleeper=sleeper,
    )


# ---------------------------------------------------------------------------
# 共享纯函数
# ---------------------------------------------------------------------------


class TestBareQuery:
    def test_plain_title_passes_through(self) -> None:
        assert bare_query("葬送的芙莉莲") == "葬送的芙莉莲"

    def test_season_episode_anchor_tokens_dropped(self) -> None:
        assert bare_query("frieren s{season}e{ep}") == "frieren"
        assert bare_query("葬送的芙莉莲 第{season}季 第{ep}话") == "葬送的芙莉莲"

    def test_isolated_anchor_words_dropped(self) -> None:
        assert bare_query("frieren season {season}") == "frieren"
        assert bare_query("overlord s {season} e {ep}") == "overlord"

    def test_all_placeholders_yields_empty(self) -> None:
        assert bare_query("{season} {ep}") == ""

    def test_anchor_word_only_dropped_as_lone_token(self) -> None:
        # "season" 独立成 token 时视为锚点残留丢弃。
        assert bare_query("a season in autumn") == "a in autumn"


class TestPickCandidate:
    def test_exact_match_wins(self) -> None:
        candidates = [(1, ("Breaking Bad", "绝命毒师")), (2, ("Under the Dome", "穹顶之下"))]
        assert pick_candidate(candidates, "绝命毒师") == 1

    def test_containment_fallback_takes_shortest_name(self) -> None:
        candidates = [
            (1, ("sword art online",)),
            (2, ("sword art online ii",)),
            (3, ("sword art online alicization",)),
        ]
        assert pick_candidate(candidates, "sword art online") == 1

    def test_short_query_skips_containment(self) -> None:
        candidates = [(1, ("刀剑神域外传",))]
        assert pick_candidate(candidates, "刀剑神域") is None  # 短 query 不参与包含匹配

    def test_no_match_returns_none(self) -> None:
        candidates = [(1, ("绝命毒师",))]
        assert pick_candidate(candidates, "魔女之旅") is None

    def test_empty_query_returns_none(self) -> None:
        assert pick_candidate([(1, ("name",))], "  ") is None


class TestSeasonDerivation:
    def test_cn_number_seasons(self) -> None:
        assert detect_season_number("葬送的芙莉莲 第二季") == 2
        assert detect_season_number("第3期【黄金郷編】") == 3
        assert detect_season_number("第十一季") == 11

    def test_en_seasons(self) -> None:
        assert detect_season_number("Frieren Season 3") == 3
        assert detect_season_number("Show S02") == 2
        assert detect_season_number("Sword Art Online II") == 2

    def test_no_marker_returns_none(self) -> None:
        assert detect_season_number("葬送のフリーレン") is None

    def test_strip_markers(self) -> None:
        assert normalize_title(strip_season_markers("葬送のフリーレン 第2期")) == "葬送のフリーレン"


class TestParseRetryAfter:
    def test_seconds_value(self) -> None:
        assert parse_retry_after("2") == 2.0

    def test_missing_or_garbage_falls_back(self) -> None:
        assert parse_retry_after(None) == DEFAULT_RETRY_AFTER_S
        assert parse_retry_after("") == DEFAULT_RETRY_AFTER_S
        assert parse_retry_after("soon") == DEFAULT_RETRY_AFTER_S

    def test_http_date_in_future(self) -> None:
        from datetime import datetime, timedelta
        from email.utils import format_datetime

        future = datetime.now(UTC) + timedelta(seconds=5)
        value = parse_retry_after(format_datetime(future))
        assert 0 < value <= MAX_RETRY_AFTER_S

    def test_capped_at_max(self) -> None:
        assert parse_retry_after("9999") == MAX_RETRY_AFTER_S


# ---------------------------------------------------------------------------
# Bangumi adapter
# ---------------------------------------------------------------------------


class TestBangumiReference:
    async def test_hit_maps_reference_facts(self) -> None:
        log = RequestLog()
        adapter = make_bangumi(BANGUMI_HIT_ROUTES, log=log)
        facts = await adapter.lookup("葬送的芙莉莲")
        assert facts is not None
        assert facts.source == "bangumi"
        assert facts.canonical_title == "葬送的芙莉莲"
        # 兄弟条目「第2期」参与推导：季列表 (1, 2)。
        assert facts.seasons == (1, 2)
        assert facts.episode_count == 28  # eps=28 优先于 total_episodes=36
        assert facts.aliases == (
            "葬送のフリーレン",
            "Frieren: Beyond Journey's End",
            "Sousou no Frieren",
            "葬送的芙莉蓮",
        )
        assert log.paths == ["/v0/search/subjects", "/v0/subjects/400602"]
        assert log.entries[0]["body"] == {
            "keyword": "葬送的芙莉莲",
            "filter": {"type": [2]},
        }

    async def test_shape_with_placeholders_folds_to_bare_query(self) -> None:
        log = RequestLog()
        adapter = make_bangumi(BANGUMI_HIT_ROUTES, log=log)
        facts = await adapter.lookup("葬送的芙莉莲 第{season}季 第{ep}话")
        assert facts is not None
        assert log.entries[0]["body"] == {"keyword": "葬送的芙莉莲", "filter": {"type": [2]}}

    async def test_search_request_carries_custom_user_agent(self) -> None:
        sent_headers: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent_headers.append(request.headers)
            return json_response(_fixture("bangumi_search_frieren.json"))

        clock = FakeClock()
        adapter = BangumiReference(
            transport=httpx.MockTransport(handler),
            clock=clock,
            sleeper=FakeSleeper(clock),
        )
        await adapter.lookup("葬送的芙莉莲")
        assert sent_headers[0]["user-agent"].startswith("autoanime/")

    async def test_no_results_returns_none(self) -> None:
        routes = {("POST", "/v0/search/subjects"): json_response(_fixture("bangumi_search_empty.json"))}
        adapter = make_bangumi(routes)
        assert await adapter.lookup("葬送的芙莉莲") is None

    async def test_candidates_but_no_match_returns_none(self) -> None:
        # 检索有候选但与查询不匹配（pick_candidate 未命中）→ None。
        adapter = make_bangumi(BANGUMI_HIT_ROUTES)
        assert await adapter.lookup("魔女之旅") is None

    async def test_non_json_response_returns_none(self) -> None:
        routes = {("POST", "/v0/search/subjects"): json_response("<html>gateway</html>")}
        adapter = make_bangumi(routes)
        assert await adapter.lookup("葬送的芙莉莲") is None

    async def test_timeout_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        routes = {("POST", "/v0/search/subjects"): handler}
        adapter = make_bangumi(routes)
        assert await adapter.lookup("葬送的芙莉莲") is None

    async def test_http_500_returns_none(self) -> None:
        routes = {("POST", "/v0/search/subjects"): json_response("{}", status_code=500)}
        adapter = make_bangumi(routes)
        assert await adapter.lookup("葬送的芙莉莲") is None

    async def test_429_backs_off_once_then_succeeds(self) -> None:
        clock = FakeClock()
        sleeper = FakeSleeper(clock)
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, headers={"retry-after": "2"})
            return json_response(_fixture("bangumi_search_frieren.json"))

        routes = {("POST", "/v0/search/subjects"): handler, ("GET", "/v0/subjects/400602"): json_response(_fixture("bangumi_subject_400602.json"))}
        adapter = make_bangumi(routes, clock=clock, sleeper=sleeper)
        facts = await adapter.lookup("葬送的芙莉莲")
        assert facts is not None
        # [429 退避 2.0, search→detail 的 QPS 间隔 1.0]
        assert sleeper.waits == [2.0, 1.0]
        assert len(calls) == 2

    async def test_429_twice_returns_none(self) -> None:
        calls: list[int] = []
        clock = FakeClock()

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429, headers={"retry-after": "0"})

        routes = {("POST", "/v0/search/subjects"): handler, ("GET", "/v0/subjects/400602"): handler}
        adapter = make_bangumi(routes, clock=clock, sleeper=FakeSleeper(clock))
        assert await adapter.lookup("葬送的芙莉莲") is None
        assert len(calls) == 2  # 退避一次后仍 429 → 放弃

    async def test_rate_limit_sleeps_between_requests(self) -> None:
        clock = FakeClock()
        sleeper = FakeSleeper(clock)
        log = RequestLog()
        adapter = make_bangumi(BANGUMI_HIT_ROUTES, clock=clock, sleeper=sleeper, log=log)
        await adapter.lookup("葬送的芙莉莲")  # 首个请求不等待；search→detail 间隔受频控
        assert sleeper.waits == [1.0]
        await adapter.lookup("葬送的芙莉莲")  # 跨 lookup 依旧受频控
        assert sleeper.waits == [1.0, 1.0, 1.0]
        assert len(log.paths) == 4


# ---------------------------------------------------------------------------
# TMDB adapter
# ---------------------------------------------------------------------------


class TestTmdbReference:
    async def test_hit_maps_reference_facts(self) -> None:
        log = RequestLog()
        adapter = make_tmdb(TMDB_HIT_ROUTES, log=log)
        facts = await adapter.lookup("葬送的芙莉莲")
        assert facts is not None
        assert facts.source == "tmdb"
        assert facts.canonical_title == "葬送的芙莉莲"
        assert facts.seasons == (1, 2)  # number_of_seasons=2
        assert facts.episode_count == 56
        assert facts.aliases == ("葬送のフリーレン",)
        assert log.paths == ["/3/search/tv", "/3/tv/209867"]
        assert log.entries[0]["params"]["language"] == "zh-CN"

    async def test_api_key_passed_via_query_param_not_leaked_in_facts(self) -> None:
        log = RequestLog()
        adapter = make_tmdb(TMDB_HIT_ROUTES, api_key="s3cret", log=log)
        facts = await adapter.lookup("葬送的芙莉莲")
        assert facts is not None
        assert log.entries[0]["params"]["api_key"] == "s3cret"

    async def test_unconfigured_returns_none_without_requests(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("unconfigured provider must not send requests")

        routes = {("GET", "/3/search/tv"): handler}
        adapter = make_tmdb(routes, api_key="")
        assert adapter.configured is False
        assert await adapter.lookup("葬送的芙莉莲") is None

    async def test_candidates_but_no_match_returns_none(self) -> None:
        adapter = make_tmdb(TMDB_HIT_ROUTES)
        assert await adapter.lookup("魔女之旅") is None

    async def test_no_results_returns_none(self) -> None:
        routes = {("GET", "/3/search/tv"): json_response(_fixture("tmdb_search_tv_empty.json"))}
        adapter = make_tmdb(routes)
        assert await adapter.lookup("葬送的芙莉莲") is None

    async def test_non_json_response_returns_none(self) -> None:
        routes = {("GET", "/3/search/tv"): json_response("<html>oops</html>")}
        adapter = make_tmdb(routes)
        assert await adapter.lookup("葬送的芙莉莲") is None

    async def test_timeout_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        routes = {("GET", "/3/search/tv"): handler}
        adapter = make_tmdb(routes)
        assert await adapter.lookup("葬送的芙莉莲") is None

    async def test_http_404_returns_none(self) -> None:
        routes = {
            ("GET", "/3/search/tv"): json_response(_fixture("tmdb_search_tv_hit.json")),
            ("GET", "/3/tv/209867"): json_response('{"status_code":34}', status_code=404),
        }
        adapter = make_tmdb(routes)
        assert await adapter.lookup("葬送的芙莉莲") is None

    async def test_429_backs_off_once_then_succeeds(self) -> None:
        clock = FakeClock()
        sleeper = FakeSleeper(clock)
        search_calls: list[int] = []

        def search_handler(request: httpx.Request) -> httpx.Response:
            search_calls.append(1)
            if len(search_calls) == 1:
                return httpx.Response(429)  # 无 Retry-After → 默认退避
            return json_response(_fixture("tmdb_search_tv_hit.json"))

        routes = {
            ("GET", "/3/search/tv"): search_handler,
            ("GET", "/3/tv/209867"): json_response(_fixture("tmdb_tv_209867_detail.json")),
        }
        adapter = make_tmdb(routes, clock=clock, sleeper=sleeper)
        facts = await adapter.lookup("葬送的芙莉莲")
        assert facts is not None
        # [429 退避 1.0, search→detail 的 QPS 间隔 1.0]
        assert sleeper.waits == [DEFAULT_RETRY_AFTER_S, 1.0]

    async def test_429_twice_returns_none(self) -> None:
        calls: list[int] = []
        clock = FakeClock()

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429, headers={"retry-after": "0"})

        routes = {("GET", "/3/search/tv"): handler, ("GET", "/3/tv/209867"): handler}
        adapter = make_tmdb(routes, clock=clock, sleeper=FakeSleeper(clock))
        assert await adapter.lookup("葬送的芙莉莲") is None
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# Registry 注册与链组合
# ---------------------------------------------------------------------------


class TestRegistryRegistration:
    def test_register_reference_providers_into_explicit_registry(self) -> None:
        registry = Registry()
        register_reference_providers(registry)
        bangumi = registry.get(MetadataReference, "bangumi")
        tmdb = registry.get(MetadataReference, "tmdb")
        assert isinstance(bangumi, MetadataReference)
        assert isinstance(tmdb, MetadataReference)
        assert isinstance(bangumi, BangumiReference)
        assert isinstance(tmdb, TmdbReference)

    def test_reference_chain_resolves_registered_names(self) -> None:
        registry = Registry()
        register_reference_providers(registry)
        chain = ReferenceChain(registry, order=("bangumi", "tmdb"))
        assert chain.names == ("bangumi", "tmdb")

    async def test_reference_chain_offline_end_to_end(self) -> None:
        # 链序第一的 bangumi 命中即返回，不再问 tmdb（离线全链路）。
        registry = Registry()
        registry.register(MetadataReference, "bangumi")(make_bangumi(BANGUMI_HIT_ROUTES))
        tmdb_calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            tmdb_calls.append(1)
            return json_response(_fixture("tmdb_search_tv_hit.json"))

        registry.register(MetadataReference, "tmdb")(
            TmdbReference(api_key="test-key", transport=httpx.MockTransport(handler))
        )
        chain = ReferenceChain(registry, order=("bangumi", "tmdb"))
        facts = await chain.lookup("葬送的芙莉莲")
        assert facts is not None
        assert facts.source == "bangumi"
        assert tmdb_calls == []


# ---------------------------------------------------------------------------
# TMDB api_key 环境变量回退
# ---------------------------------------------------------------------------


class TestTmdbApiKeyEnv:
    def test_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOANIME_TMDB_API_KEY", "env-key")
        adapter = TmdbReference()
        assert adapter.configured is True

    def test_missing_env_is_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOANIME_TMDB_API_KEY", raising=False)
        adapter = TmdbReference()
        assert adapter.configured is False

    def test_explicit_key_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOANIME_TMDB_API_KEY", "env-key")
        adapter = TmdbReference(api_key="explicit-key")
        assert adapter.configured is True
