"""PR5 T3：L3 LLM provider 实现的单元测试（全部离线）。

- 识别器（l3_llm.LlmFallbackRecognizer）：fake transport + fake cache
  store，录制响应回放 tests/fixtures/l3（T1 录制的 LLM 文本）；
- transport（providers.llm.HttpxLlmTransport）：``httpx.MockTransport``
  注入 tests/fixtures/llm 录制的 chat completion 响应；
- Registry 注册：providers.register_providers。

无任何真实网络调用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from autoanime.config import Settings
from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import (
    L3Recognizer,
    LlmTransport,
    ParseContext,
    ParseResult,
    RawName,
    Registry,
)
from autoanime.pipeline.l3 import LlmCache, llm_cache_key
from autoanime.pipeline.l3_llm import LlmFallbackRecognizer
from autoanime.providers import LLM_TRANSPORT_NAME, register_providers
from autoanime.providers.llm import HttpxLlmTransport, LlmTransportError, safe_origin

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"
L3_ROOT = FIXTURE_ROOT / "l3"
LLM_ROOT = FIXTURE_ROOT / "llm"

API_KEY_VALUE = "sk-test-secret-0123456789abcdef"
BASE_URL = "https://llm.example.invalid/v1/secret-path"


# ---------------------------------------------------------------------------
# fakes: scripted transport + in-memory cache store
# ---------------------------------------------------------------------------


@dataclass
class ScriptedTransport:
    """按脚本回放响应文本或异常；记录每次 complete 调用。"""

    script: list[str | Exception]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str:
        self.calls.append({"prompt": prompt, "model": model, "timeout_s": timeout_s})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def exhausted(self) -> bool:
        return not self.script


@dataclass
class MemoryCacheStore:
    """llm_cache 的内存 fake；可注入 get/put 失败。"""

    entries: dict[str, LlmCache] = field(default_factory=dict)
    puts: list[LlmCache] = field(default_factory=list)
    fail_get: bool = False
    fail_put: bool = False

    async def get(self, pattern_hash: str) -> LlmCache | None:
        if self.fail_get:
            raise RuntimeError("cache get unavailable")
        return self.entries.get(pattern_hash)

    async def put(self, cache: LlmCache) -> None:
        if self.fail_put:
            raise RuntimeError("cache put unavailable")
        self.entries[cache.pattern_hash] = cache
        self.puts.append(cache)


def _l3_fixtures() -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(L3_ROOT.glob("*.json"))
    ]


def _llm_fixture(name: str) -> dict[str, Any]:
    return json.loads((LLM_ROOT / name).read_text(encoding="utf-8"))


def _make_recognizer(**kwargs: Any) -> LlmFallbackRecognizer:
    return LlmFallbackRecognizer(model="test-model", **kwargs)


def _result_eq_expected(result: ParseResult | None, expected: dict[str, Any]) -> None:
    assert result is not None
    assert result.title == expected["title"]
    assert result.season == expected["season"]
    assert result.episode == expected["episode"]
    assert result.segment == Segment(expected["segment"])
    assert result.fansub == expected["fansub"]
    assert result.level.value == expected["level"]
    assert result.confidence == pytest.approx(expected["confidence"])
    assert list(result.missing_fields) == expected["missing_fields"]
    assert result.evidence == expected["evidence"]
    assert set(result.evidence.values()) == {"llm"}


# ---------------------------------------------------------------------------
# recognizer: fixture 驱动的合法 / 纠正 / 放弃 流程
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture", _l3_fixtures(), ids=lambda f: f["id"]
)
async def test_recognizer_fixture_flow(fixture: dict[str, Any]) -> None:
    """五类主流程（合法/纠正后成功/纠正后失败/可选缺省）+ L3_03 放弃。"""
    transport = ScriptedTransport(
        [fixture["llm_response"], fixture.get("correction_response", "")]
    )
    store = MemoryCacheStore()
    recognizer = _make_recognizer()
    raw = RawName(name=fixture["query"]["name"])

    result = await recognizer.enhance(raw, None, None, transport, store, operation_id="op-1")

    expected = fixture["query"]["expected"]
    if expected is None:
        # L3_03：纠正后仍非法 → 放弃并计数，不落缓存
        assert result is None
        assert recognizer.parse_failure_count == 1
        assert recognizer.unavailable_count == 0
        assert transport.call_count == 2
        assert store.puts == []
    else:
        _result_eq_expected(result, expected)
        # 只有 schema 合法的真实调用响应落缓存
        assert len(store.puts) == 1
        assert store.puts[0].pattern_hash == llm_cache_key(fixture["query"]["name"])
        assert store.puts[0].model == "test-model"


async def test_recognizer_records_prompt_and_model() -> None:
    fixture = _l3_fixtures()[0]
    transport = ScriptedTransport([fixture["llm_response"]])
    store = MemoryCacheStore()
    recognizer = _make_recognizer()

    await recognizer.enhance(
        RawName(name=fixture["query"]["name"]),
        None,
        ParseContext(fansub_pref="LoliHouse"),
        transport,
        store,
    )

    assert transport.call_count == 1
    call = transport.calls[0]
    assert call["model"] == "test-model"
    assert call["timeout_s"] == pytest.approx(10.0)
    assert fixture["query"]["name"] in call["prompt"]
    assert "preferred fansub: LoliHouse" in call["prompt"]
    assert "Local parsing produced no result." in call["prompt"]


async def test_recognizer_passes_l1_hint() -> None:
    """带 L1 结果时 prompt 含提示；result=None 与 hint 无关地独立成 L3 结果。"""
    fixture = _l3_fixtures()[0]
    l1 = ParseResult(
        title="?",
        season=None,
        episode=None,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.LOW,
        confidence=0.0,
    )
    transport = ScriptedTransport([fixture["llm_response"]])
    result = await _make_recognizer().enhance(
        RawName(name=fixture["query"]["name"]), l1, None, transport, MemoryCacheStore()
    )
    assert result is not None
    assert "Local parse hint" in transport.calls[0]["prompt"]
    assert result.evidence == {k: "llm" for k in result.evidence}


# ---------------------------------------------------------------------------
# recognizer: 超时 / 网络重试
# ---------------------------------------------------------------------------


async def test_recognizer_retries_transport_failure_then_succeeds() -> None:
    """失败 1 次（重试上限 2）后成功；网络重试不计入 calls_used。"""
    boom = TimeoutError("simulated timeout")
    transport = ScriptedTransport([boom, "not json", '{"title": "T", "segment": "episode"}'])
    store = MemoryCacheStore()
    recognizer = _make_recognizer()

    result = await recognizer.enhance(
        RawName(name="Some.Release.S01E01.mkv"), None, None, transport, store
    )

    assert result is not None
    assert result.title == "T"
    assert transport.call_count == 3
    assert recognizer.calls_used == 1
    assert recognizer.unavailable_count == 0
    assert len(store.puts) == 1


async def test_recognizer_gives_up_after_retry_budget() -> None:
    """连续失败耗尽重试：``transport_retry_allowed(2)`` 为 False，共 2 次尝试。"""
    boom = TimeoutError("simulated timeout")
    transport = ScriptedTransport([boom, boom])
    store = MemoryCacheStore()
    recognizer = _make_recognizer()

    result = await recognizer.enhance(
        RawName(name="Some.Release.S01E01.mkv"), None, None, transport, store
    )

    assert result is None
    assert transport.call_count == 2
    assert recognizer.unavailable_count == 1
    assert recognizer.calls_used == 0
    assert store.puts == []


async def test_transport_failure_log_carries_sanitized_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """transport 失败日志：``LlmTransportError`` 输出脱敏消息（可区分
    超时/额度/网络），未知异常只打类型名。"""
    boom = LlmTransportError("llm request failed (ReadTimeout) at https://example.invalid")
    transport = ScriptedTransport([boom, boom])
    store = MemoryCacheStore()
    recognizer = _make_recognizer()

    with caplog.at_level("WARNING", logger="autoanime.pipeline.l3_llm"):
        result = await recognizer.enhance(
            RawName(name="Some.Release.S01E01.mkv"), None, None, transport, store
        )

    assert result is None
    warnings = [r for r in caplog.records if "transport unavailable" in r.getMessage()]
    assert len(warnings) == 1
    assert "ReadTimeout" in warnings[0].getMessage()
    assert "example.invalid" in warnings[0].getMessage()


async def test_transport_failure_log_masks_unknown_exception_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """非 ``LlmTransportError`` 的未知异常：日志只打类型名，不透传消息。"""
    boom = RuntimeError("raw secret detail must not leak")
    transport = ScriptedTransport([boom, boom])
    store = MemoryCacheStore()
    recognizer = _make_recognizer()

    with caplog.at_level("WARNING", logger="autoanime.pipeline.l3_llm"):
        result = await recognizer.enhance(
            RawName(name="Some.Release.S01E01.mkv"), None, None, transport, store
        )

    assert result is None
    warnings = [r for r in caplog.records if "transport unavailable" in r.getMessage()]
    assert len(warnings) == 1
    assert "RuntimeError" in warnings[0].getMessage()
    assert "raw secret detail" not in warnings[0].getMessage()


async def test_recognizer_dirty_cache_falls_through_to_real_call() -> None:
    """脏缓存（响应非法）按 miss 继续真实调用，成功后覆盖写回。"""
    raw = RawName(name="Dirty.Cache.Release.S01E02.mkv")
    store = MemoryCacheStore(
        entries={
            llm_cache_key(raw.name): LlmCache(
                pattern_hash=llm_cache_key(raw.name),
                response="definitely not json",
                model="old-model",
            )
        }
    )
    valid = '{"title": "Clean Title", "segment": "movie"}'
    transport = ScriptedTransport([valid])
    recognizer = _make_recognizer()

    result = await recognizer.enhance(raw, None, None, transport, store)

    assert result is not None
    assert result.title == "Clean Title"
    assert transport.call_count == 1
    assert len(store.puts) == 1
    assert store.entries[llm_cache_key(raw.name)].response == valid


async def test_recognizer_cache_hit_skips_transport() -> None:
    """缓存命中：不调 transport、不写 cache，直接回放解析。"""
    raw = RawName(name="Cached.Release.S01E03.mkv")
    cached_text = '{"title": "Cached Title", "season": 1, "segment": "episode"}'
    store = MemoryCacheStore(
        entries={
            llm_cache_key(raw.name): LlmCache(
                pattern_hash=llm_cache_key(raw.name), response=cached_text
            )
        }
    )
    transport = ScriptedTransport([])
    recognizer = _make_recognizer()

    result = await recognizer.enhance(raw, None, None, transport, store)

    assert result is not None
    assert result.title == "Cached Title"
    assert result.season == 1
    assert transport.call_count == 0
    assert store.puts == []


async def test_recognizer_cache_failure_degrades() -> None:
    """cache get/put 异常不阻断：get 失败按 miss 真实调用，put 失败仍返回结果。"""
    raw = RawName(name="Cache.Down.Release.S01E04.mkv")
    valid = '{"title": "Still Works", "segment": "episode"}'
    transport = ScriptedTransport([valid])
    store = MemoryCacheStore(fail_get=True, fail_put=True)
    recognizer = _make_recognizer()

    result = await recognizer.enhance(raw, None, None, transport, store)

    assert result is not None
    assert result.title == "Still Works"
    assert transport.call_count == 1


# ---------------------------------------------------------------------------
# recognizer: 预算（超限只记 audit 不阻断）
# ---------------------------------------------------------------------------


async def test_recognizer_budget_exceeded_logs_but_does_not_block(
    caplog: pytest.LogCaptureFixture,
) -> None:
    valid = '{"title": "T", "segment": "episode"}'
    recognizer = _make_recognizer(budget=1)

    with caplog.at_level("WARNING", logger="autoanime.pipeline.l3_llm"):
        first = await recognizer.enhance(
            RawName(name="Budget.One.S01E01.mkv"), None, None,
            ScriptedTransport([valid]), MemoryCacheStore(),
        )
        second = await recognizer.enhance(
            RawName(name="Budget.Two.S01E02.mkv"), None, None,
            ScriptedTransport([valid]), MemoryCacheStore(),
        )

    assert first is not None and second is not None
    assert recognizer.calls_used == 2
    warnings = [r for r in caplog.records if "budget exceeded" in r.getMessage()]
    assert len(warnings) == 1
    assert "not blocking" in warnings[0].getMessage()


async def test_recognizer_no_budget_warning_when_unlimited(
    caplog: pytest.LogCaptureFixture,
) -> None:
    valid = '{"title": "T", "segment": "episode"}'
    recognizer = _make_recognizer(budget=None)
    with caplog.at_level("WARNING", logger="autoanime.pipeline.l3_llm"):
        await recognizer.enhance(
            RawName(name="No.Budget.S01E01.mkv"), None, None,
            ScriptedTransport([valid]), MemoryCacheStore(),
        )
    assert not [r for r in caplog.records if "budget exceeded" in r.getMessage()]


# ---------------------------------------------------------------------------
# recognizer: 未启用 / 缺 model
# ---------------------------------------------------------------------------


async def test_recognizer_disabled_returns_none_without_calls() -> None:
    transport = ScriptedTransport([])
    recognizer = LlmFallbackRecognizer(enabled=False, model="m")
    result = await recognizer.enhance(
        RawName(name="Whatever.S01E01.mkv"), None, None, transport, MemoryCacheStore()
    )
    assert result is None
    assert transport.call_count == 0


async def test_recognizer_missing_model_returns_none() -> None:
    transport = ScriptedTransport([])
    recognizer = LlmFallbackRecognizer(enabled=True, model=None)
    result = await recognizer.enhance(
        RawName(name="Whatever.S01E01.mkv"), None, None, transport, MemoryCacheStore()
    )
    assert result is None
    assert transport.call_count == 0


async def test_recognizer_satisfies_protocol() -> None:
    assert isinstance(_make_recognizer(), L3Recognizer)


async def test_recognizer_from_settings() -> None:
    settings = Settings(
        llm_enabled=True, llm_model="m1", llm_timeout_s=7.5, llm_budget=5,
        llm_max_retries=1,
    )
    recognizer = LlmFallbackRecognizer.from_settings(settings)
    assert recognizer._enabled is True
    assert recognizer._model == "m1"
    assert recognizer._timeout_s == pytest.approx(7.5)
    assert recognizer._budget == 5
    assert recognizer._max_retries == 1


async def test_transport_retry_honors_max_retries() -> None:
    """llm_max_retries 语义 = 最大总尝试次数（默认 2：初次+重试 1 次），配置可下调。"""
    transport = ScriptedTransport([RuntimeError("boom")] * 3)
    recognizer = _make_recognizer(max_retries=1)
    result = await recognizer.enhance(
        RawName(name="Retry.Config.S01E01.mkv"), None, None, transport, MemoryCacheStore()
    )
    assert result is None
    # max_retries=1 → 仅初次 1 次调用，重试不发生
    assert transport.call_count == 1
    assert recognizer.unavailable_count == 1


# ---------------------------------------------------------------------------
# transport: httpx.MockTransport + 录制 fixture（离线）
# ---------------------------------------------------------------------------


def _mock_handler(fixture: dict[str, Any], seen: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(fixture["status"], json=fixture["body"])

    return httpx.MockTransport(handler)


async def test_transport_valid_fixture() -> None:
    fixture = _llm_fixture("openai_chat_completion_valid.json")
    seen: dict[str, Any] = {}
    transport = HttpxLlmTransport(
        BASE_URL,
        SecretStr(API_KEY_VALUE),
        client=httpx.AsyncClient(transport=_mock_handler(fixture, seen)),
    )
    content = await transport.complete("parse this", model="gpt-4o-mini", timeout_s=10.0)

    body = json.loads(fixture["body"]["choices"][0]["message"]["content"])
    assert json.loads(content) == body
    assert seen["auth"] == f"Bearer {API_KEY_VALUE}"
    assert seen["url"].startswith(safe_origin(BASE_URL))
    assert seen["payload"]["model"] == "gpt-4o-mini"
    assert seen["payload"]["messages"][0]["content"] == "parse this"
    await transport.aclose()


async def test_transport_server_error_sanitized() -> None:
    """上游错误 → LlmTransportError；消息不含 key、不含完整 base_url、不含错误体。"""
    fixture = _llm_fixture("openai_chat_completion_server_error.json")
    seen: dict[str, Any] = {}
    transport = HttpxLlmTransport(
        BASE_URL,
        SecretStr(API_KEY_VALUE),
        client=httpx.AsyncClient(transport=_mock_handler(fixture, seen)),
    )

    with pytest.raises(LlmTransportError) as exc_info:
        await transport.complete("p", model="m", timeout_s=10.0)

    message = str(exc_info.value)
    assert API_KEY_VALUE not in message
    assert BASE_URL not in message
    assert "secret-path" not in message
    assert "should-never-leak" not in message
    assert safe_origin(BASE_URL) in message
    await transport.aclose()


async def test_transport_bad_shape() -> None:
    """HTTP 200 但缺 choices → LlmTransportError。"""
    fixture = _llm_fixture("openai_chat_completion_bad_shape.json")
    transport = HttpxLlmTransport(
        BASE_URL,
        SecretStr(API_KEY_VALUE),
        client=httpx.AsyncClient(transport=_mock_handler(fixture, {})),
    )

    with pytest.raises(LlmTransportError):
        await transport.complete("p", model="m", timeout_s=10.0)
    await transport.aclose()


async def test_transport_no_api_key_still_sends() -> None:
    """未配置 api_key：不带 Authorization 头仍可调用。"""
    fixture = _llm_fixture("openai_chat_completion_valid.json")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(fixture["status"], json=fixture["body"])

    transport = HttpxLlmTransport(
        BASE_URL, None, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    content = await transport.complete("p", model="m", timeout_s=5.0)
    assert content
    assert seen["auth"] is None
    await transport.aclose()


async def test_transport_satisfies_protocol_and_redacted_repr() -> None:
    transport = HttpxLlmTransport(BASE_URL, SecretStr(API_KEY_VALUE))
    assert isinstance(transport, LlmTransport)
    r = repr(transport)
    assert API_KEY_VALUE not in r
    assert "api_key=***" in r
    assert "secret-path" not in r
    await transport.aclose()


def test_safe_origin_strips_path_and_query() -> None:
    assert safe_origin("https://user:pw@host.example/x/y?token=z") == "https://host.example"
    assert safe_origin("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"


# ---------------------------------------------------------------------------
# registry 注册
# ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_enabled": True,
        "llm_model": "gpt-4o-mini",
        "llm_base_url": "https://llm.example.invalid/v1",
        "llm_api_key": SecretStr(API_KEY_VALUE),
    }
    values.update(overrides)
    return Settings(**values)


def test_register_providers_enabled() -> None:
    registry = Registry()
    assert register_providers(registry, _settings()) is True
    transport = registry.get(LlmTransport, LLM_TRANSPORT_NAME)
    assert isinstance(transport, HttpxLlmTransport)


def test_register_providers_disabled() -> None:
    registry = Registry()
    assert register_providers(registry, _settings(llm_enabled=False)) is False
    assert registry.optional(LlmTransport) is None


def test_register_providers_missing_endpoint() -> None:
    registry = Registry()
    assert register_providers(registry, _settings(llm_base_url=None)) is False
    assert register_providers(registry, _settings(llm_model=None)) is False
    assert registry.optional(LlmTransport) is None
