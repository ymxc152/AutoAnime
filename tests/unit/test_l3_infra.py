"""PR5 T1：L3 公共契约与纯函数基础设施的单元测试（全部离线）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import (
    L3Recognizer,
    LlmCacheStore,
    LlmTransport,
    MetadataReference,
    ParseContext,
    ParseResult,
    RawName,
    Registry,
)
from autoanime.pipeline.l2.bypass import pattern_hash
from autoanime.pipeline.l3 import (
    L3_EVIDENCE,
    LLM_MAX_RETRIES,
    LLM_SCHEMA_CORRECTION_RETRIES,
    LLM_TIMEOUT_S,
    REASON_MISSING_FIELD,
    REASON_NOT_JSON,
    REASON_TYPE_ERROR,
    REASON_UNKNOWN_FIELD,
    ArbiterInput,
    L3Draft,
    LlmCache,
    LlmResponseError,
    ReferenceChain,
    ReferenceFacts,
    apply_l3_draft,
    arbitrate,
    budget_exceeded,
    build_correction_prompt,
    build_prompt,
    disambiguate_season,
    evidence_rank,
    l3_parse_result,
    llm_cache_key,
    parse_llm_response,
    resolve_field,
    schema_correction_allowed,
    title_shape_matches,
    transport_retry_allowed,
    upgrade_level,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "l3"


# ---------------------------------------------------------------------------
# schema: strict LLM response parsing
# ---------------------------------------------------------------------------


def test_parse_full_valid_response() -> None:
    draft = parse_llm_response(
        '{"title": "Sousou no Frieren", "season": 1, "episode": 3, '
        '"segment": "episode", "fansub": "LoliHouse"}'
    )

    assert draft == L3Draft(
        title="Sousou no Frieren",
        season=1,
        episode=3,
        segment=Segment.EPISODE,
        fansub="LoliHouse",
    )


def test_parse_missing_optional_fields_fill_none() -> None:
    draft = parse_llm_response('{"title": "Mushoku Tensei", "segment": "season_pack"}')

    assert draft.title == "Mushoku Tensei"
    assert draft.segment is Segment.SEASON_PACK
    assert draft.season is None
    assert draft.episode is None
    assert draft.fansub is None


def test_parse_null_fields_are_unknown() -> None:
    draft = parse_llm_response(
        '{"title": "X", "season": null, "episode": null, '
        '"segment": "movie", "fansub": null}'
    )

    assert draft.season is None and draft.episode is None and draft.fansub is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("plain text, no json", REASON_NOT_JSON),
        ("[1, 2, 3]", REASON_NOT_JSON),
        ('{"title": "X", "segment": "episode", "confidence": 0.9}', REASON_UNKNOWN_FIELD),
        ('{"title": "X", "segment": "episode", "extra": 1}', REASON_UNKNOWN_FIELD),
        ('{"title": "X", "segment": "ova"}', REASON_TYPE_ERROR),
        ('{"title": 42, "segment": "episode"}', REASON_TYPE_ERROR),
        ('{"title": "X", "season": true, "segment": "episode"}', REASON_TYPE_ERROR),
        ('{"title": "X", "season": 1.5, "segment": "episode"}', REASON_TYPE_ERROR),
        ('{"title": "X", "fansub": "", "segment": "episode"}', REASON_TYPE_ERROR),
        ('{"segment": "episode"}', REASON_MISSING_FIELD),
        ('{"title": "X"}', REASON_MISSING_FIELD),
        ('{"title": "  ", "segment": "episode"}', REASON_TYPE_ERROR),
    ],
)
def test_parse_invalid_responses(text: str, reason: str) -> None:
    with pytest.raises(LlmResponseError) as exc_info:
        parse_llm_response(text)

    assert exc_info.value.reason == reason


# ---------------------------------------------------------------------------
# prompt: pure construction, no secrets
# ---------------------------------------------------------------------------


def _sample_result() -> ParseResult:
    return ParseResult(
        title="Some Anime",
        season=None,
        episode=3,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.MEDIUM,
        confidence=0.6,
        evidence={"title": "name", "episode": "name"},
    )


def test_build_prompt_contains_inputs_and_schema() -> None:
    prompt = build_prompt(
        "Some.Anime.-.03.[Group].mkv",
        _sample_result(),
        ParseContext(fansub_pref="Group"),
    )

    assert "Some.Anime.-.03.[Group].mkv" in prompt
    assert "Some Anime" in prompt
    assert "Group" in prompt
    for field_name in ("title", "season", "episode", "segment", "fansub"):
        assert f'"{field_name}"' in prompt


def test_build_prompt_without_l1_and_context() -> None:
    prompt = build_prompt("raw.mkv", None, None)

    assert "raw.mkv" in prompt
    assert "produced no result" in prompt
    assert "No extra context." in prompt


def test_build_prompt_is_deterministic() -> None:
    args = ("Some.Anime.-.03.mkv", _sample_result(), ParseContext(release_progress=12))

    assert build_prompt(*args) == build_prompt(*args)


def test_build_correction_prompt_replays_inputs() -> None:
    previous = build_prompt("Some.Anime.-.03.mkv", None, None)
    correction = build_correction_prompt(previous, "not json", REASON_NOT_JSON)

    assert REASON_NOT_JSON in correction
    assert "not json" in correction
    assert previous in correction
    assert '"segment"' in correction


def test_prompt_templates_never_embed_secrets() -> None:
    prompt = build_prompt("x.mkv", None, None)
    correction = build_correction_prompt(prompt, "bad", REASON_TYPE_ERROR)

    for text in (prompt, correction):
        assert "llm_api_key" not in text
        assert "sk-" not in text


# ---------------------------------------------------------------------------
# budget: timeout / retry / budget decisions
# ---------------------------------------------------------------------------


def test_budget_constants_match_contract() -> None:
    assert LLM_TIMEOUT_S == 10.0
    assert LLM_MAX_RETRIES == 2
    assert LLM_SCHEMA_CORRECTION_RETRIES == 1


def test_transport_retry_window() -> None:
    assert transport_retry_allowed(0) is True
    assert transport_retry_allowed(1) is True
    assert transport_retry_allowed(2) is False


def test_schema_correction_window() -> None:
    assert schema_correction_allowed(0) is True
    assert schema_correction_allowed(1) is False


def test_budget_exceeded_is_audit_only() -> None:
    assert budget_exceeded(0, None) is False
    assert budget_exceeded(1000, None) is False
    assert budget_exceeded(3, 3) is False
    assert budget_exceeded(4, 3) is True


# ---------------------------------------------------------------------------
# cache_key: bypass-compatible normalization
# ---------------------------------------------------------------------------


def test_llm_cache_key_matches_bypass_pattern_hash() -> None:
    raw = "[LoliHouse] Sousou no Frieren - 03 [1080p].mkv"

    assert llm_cache_key(raw) == pattern_hash(raw)
    assert llm_cache_key(raw) == llm_cache_key("[lolihouse] Sousou.no.Frieren.-.03.[1080p].mkv")


def test_llm_cache_structure() -> None:
    cache = LlmCache(pattern_hash="abc", response='{"title": "X"}', model="test-model")

    assert cache.pattern_hash == "abc"
    assert cache.response.startswith("{")
    assert cache.model == "test-model"
    assert LlmCache(pattern_hash="abc", response="r").model is None


# ---------------------------------------------------------------------------
# draft: L3Draft -> ParseResult
# ---------------------------------------------------------------------------


def _draft() -> L3Draft:
    return L3Draft(
        title="Sousou no Frieren",
        season=1,
        episode=3,
        segment=Segment.EPISODE,
        fansub="LoliHouse",
    )


def test_l3_parse_result_stands_alone_at_medium() -> None:
    result = l3_parse_result(_draft())

    assert result.title == "Sousou no Frieren"
    assert result.season == 1 and result.episode == 3
    assert result.segment is Segment.EPISODE
    assert result.fansub == "LoliHouse"
    assert result.level is Confidence.MEDIUM
    assert result.confidence == 0.6
    assert result.missing_fields == ()
    assert result.evidence == {name: L3_EVIDENCE for name in (
        "title", "season", "episode", "segment", "fansub"
    )}


def test_l3_parse_result_records_missing_fields() -> None:
    result = l3_parse_result(
        L3Draft(title="Mushoku Tensei", segment=Segment.EPISODE, season=2)
    )

    assert result.missing_fields == ("episode",)
    assert set(result.evidence) == {"title", "season", "segment"}


def test_apply_l3_draft_fills_absent_fields_only() -> None:
    base = ParseResult(
        title="Some Anime",
        season=None,
        episode=None,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=("season",),
        evidence={"title": "name", "episode": "none", "segment": "name", "season": "none"},
    )

    merged = apply_l3_draft(base, _draft())

    assert merged.title == "Some Anime"  # never overwrites an existing value
    assert merged.season == 1
    assert merged.episode == 3
    assert merged.fansub == "LoliHouse"
    assert merged.evidence["season"] == L3_EVIDENCE
    assert merged.evidence["title"] == "name"
    assert merged.level is Confidence.MEDIUM  # upgrades are the arbiter's call
    assert merged.missing_fields == ()


def test_apply_l3_draft_never_touches_name_folder_evidence() -> None:
    base = ParseResult(
        title="Some Anime",
        season=None,
        episode=None,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=("season",),
        evidence={"title": "name", "season": "folder", "segment": "name"},
    )

    merged = apply_l3_draft(base, _draft())

    assert merged.title == "Some Anime"
    assert merged.season is None  # folder evidence stays protected even when absent
    assert merged.segment is Segment.EPISODE  # name evidence is never overwritten
    assert merged.fansub == "LoliHouse"  # unprotected absence gets filled
    assert merged.evidence["season"] == "folder"


def test_apply_l3_draft_with_none_builds_standalone() -> None:
    assert apply_l3_draft(None, _draft()) == l3_parse_result(_draft())


# ---------------------------------------------------------------------------
# reference: ReferenceFacts + ReferenceChain
# ---------------------------------------------------------------------------


@dataclass
class _FakeReference:
    facts: ReferenceFacts | None
    calls: list[str]

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        self.calls.append(title_shape)
        return self.facts


def _registry_with(**providers: object) -> Registry:
    registry = Registry()
    for name, provider in providers.items():
        registry.register(MetadataReference, name)(provider)
    return registry


async def test_reference_chain_first_hit_wins_in_order() -> None:
    bangumi = _FakeReference(ReferenceFacts(canonical_title="BG", source="bangumi"), [])
    tmdb = _FakeReference(ReferenceFacts(canonical_title="TT", source="tmdb"), [])
    chain = ReferenceChain(_registry_with(tmdb=tmdb, bangumi=bangumi), order=["bangumi", "tmdb"])

    facts = await chain.lookup("sousou no frieren")

    assert facts is not None and facts.canonical_title == "BG"
    assert chain.names == ("bangumi", "tmdb")
    assert bangumi.calls == ["sousou no frieren"]
    assert tmdb.calls == []  # first hit short-circuits


async def test_reference_chain_falls_through_misses() -> None:
    first = _FakeReference(None, [])
    second = _FakeReference(ReferenceFacts(canonical_title="TT", source="tmdb"), [])
    chain = ReferenceChain(_registry_with(a=first, b=second), order=["a", "b"])

    facts = await chain.lookup("shape")

    assert facts is not None and facts.source == "tmdb"
    assert first.calls == ["shape"] and second.calls == ["shape"]


async def test_reference_chain_disabled_returns_none_without_calls() -> None:
    provider = _FakeReference(ReferenceFacts(canonical_title="X"), [])
    chain = ReferenceChain(_registry_with(bangumi=provider), enabled=False)

    assert await chain.lookup("shape") is None
    assert provider.calls == []


async def test_reference_chain_skips_unregistered_names() -> None:
    provider = _FakeReference(None, [])
    chain = ReferenceChain(
        _registry_with(tmdb=provider), order=["bangumi", "tmdb", "anilist"]
    )

    assert chain.names == ("tmdb",)
    assert await chain.lookup("shape") is None


# ---------------------------------------------------------------------------
# arbiter: signatures fixed in T1 (bodies are T4)
# ---------------------------------------------------------------------------


def test_evidence_rank_follows_priority_order() -> None:
    ranks = [evidence_rank(source) for source in ("name", "folder", "context", "memory", "llm")]

    assert ranks == sorted(ranks)
    assert evidence_rank(None) > evidence_rank("llm")
    assert evidence_rank("none") > evidence_rank("llm")
    assert evidence_rank("mystery") > evidence_rank("llm")


def test_title_shape_matches_normalized_titles() -> None:
    assert title_shape_matches("Sousou no Frieren", "sousou  no frieren")
    assert title_shape_matches("Frieren S1", "frieren s02")
    assert not title_shape_matches("Sousou no Frieren", "Spy x Family")


def test_arbiter_bodies_are_t4() -> None:
    data = ArbiterInput(
        raw=RawName(name="x.mkv"), l1_result=None, fused=None, l3_result=None
    )

    with pytest.raises(NotImplementedError):
        arbitrate(data)
    with pytest.raises(NotImplementedError):
        resolve_field("title", l1=None, fused=None, l3=None)
    with pytest.raises(NotImplementedError):
        upgrade_level(l1=None, fused=_sample_result(), l3=None, reference=None)
    with pytest.raises(NotImplementedError):
        disambiguate_season(memory_seasons=(1, 2), l3=None, reference=None)


# ---------------------------------------------------------------------------
# protocols: fakes satisfy the L3 contracts
# ---------------------------------------------------------------------------


@dataclass
class _FakeTransport:
    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str:
        return "{}"


@dataclass
class _FakeCacheStore:
    async def get(self, pattern_hash: str) -> LlmCache | None:
        return None

    async def put(self, cache: LlmCache) -> None:
        return None


@dataclass
class _FakeL3Recognizer:
    async def enhance(
        self,
        raw: RawName,
        result: ParseResult | None,
        context: ParseContext | None,
        transport: LlmTransport,
        cache_store: LlmCacheStore,
        *,
        operation_id: str | None = None,
    ) -> ParseResult | None:
        return None


def test_fakes_satisfy_l3_protocols() -> None:
    assert isinstance(_FakeTransport(), LlmTransport)
    assert isinstance(_FakeCacheStore(), LlmCacheStore)
    assert isinstance(_FakeReference(None, []), MetadataReference)
    assert isinstance(_FakeL3Recognizer(), L3Recognizer)


# ---------------------------------------------------------------------------
# fixtures: tests/fixtures/l3 roundtrip schema
# ---------------------------------------------------------------------------


def _fixture_paths() -> list[Path]:
    return sorted(FIXTURE_ROOT.glob("*.json"))


def _resolve_response(payload: dict[str, Any]) -> Any:
    """Replay the recorded response sequence: first parse, then correction."""
    try:
        return parse_llm_response(payload["llm_response"])
    except LlmResponseError:
        correction = payload.get("correction_response")
        if correction is None:
            return None
        return parse_llm_response(correction)


@pytest.mark.parametrize("path", _fixture_paths(), ids=lambda path: path.stem)
def test_l3_fixture_roundtrip(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["id"] == path.stem
    assert isinstance(payload["llm_response"], str) and payload["llm_response"]

    query = payload["query"]
    assert isinstance(query["name"], str) and query["name"]

    try:
        draft = _resolve_response(payload)
    except LlmResponseError:
        draft = None

    expected = query["expected"]
    if expected is None:
        assert draft is None
        return

    assert draft is not None
    result = l3_parse_result(draft)
    assert result.title == expected["title"]
    assert result.season == expected["season"]
    assert result.episode == expected["episode"]
    assert result.segment.value == expected["segment"]
    assert result.fansub == expected["fansub"]
    assert result.level.value == expected["level"]
    assert result.confidence == expected["confidence"]
    assert list(result.missing_fields) == expected["missing_fields"]
    assert result.evidence == expected["evidence"]


def test_l3_fixture_coverages() -> None:
    ids = {path.stem for path in _fixture_paths()}

    assert {
        "L3_01_valid_response",
        "L3_02_non_json_then_corrected",
        "L3_03_non_json_gives_up",
        "L3_04_unknown_field_then_corrected",
        "L3_05_missing_optional_fields",
    } <= ids
