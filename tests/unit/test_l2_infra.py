"""Unit tests for the L2 memory-layer contracts and pure infrastructure (PR4 T1)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import MemoryRecognizer, MemoryStore, ParseResult, RawName
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l2 import (
    EPISODE_PLACEHOLDER,
    KEY_LEVEL_EXACT,
    KEY_LEVEL_SERIES,
    SEASON_PLACEHOLDER,
    TRUST_FUSION_THRESHOLD,
    TRUST_PENDING_THRESHOLD,
    MemoryHit,
    apply_memory_hit,
    backfill_title,
    build_title_shape,
    can_fuse,
    eligible_for_memory,
    fansub_norm,
    fused_level,
    is_bypassed,
    key_hash,
    level1_key,
    level2_key,
    normalize_pattern,
    pattern_hash,
    should_demote_to_pending,
    stable_hash,
    trust_score,
)
from tests.support.fixtures import (
    FixtureError,
    load_roundtrip_case,
    load_roundtrip_cases,
)

ROUNDTRIP_ROOT = Path(__file__).parents[1] / "fixtures" / "memory"


def _bleach_result() -> ParseResult:
    """L1 output shape of the B01 sample: MEDIUM, season missing."""
    return ParseResult(
        title="BLEACH Sennen Kessen-hen",
        season=None,
        episode=42,
        segment=Segment.EPISODE,
        fansub="BeanSub&FZSD&LoliHouse",
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=("season",),
        evidence={
            "title": "name",
            "season": "none",
            "episode": "name",
            "segment": "name",
            "fansub": "name",
        },
    )


# --- keys -------------------------------------------------------------------


def test_level1_key_is_casefolded_title_shape_without_fansub() -> None:
    assert level1_key("Anime AzurLane Slow Ahead") == "anime azurlane slow ahead"
    assert level1_key("Anime.AzurLane.Slow.Ahead") == level1_key("Anime AzurLane Slow Ahead")


def test_level1_key_abstracts_season_and_episode_numbers() -> None:
    assert level1_key("Show S02E01") == "show s{season}e{ep}"
    assert level1_key("作品 第01话") == "作品 第{ep}话"
    assert level1_key("Title - 41") == "title - {ep}"


def test_level2_key_includes_structure_and_fansub_norm() -> None:
    key = level2_key("Show S02E01", 2, 1, "MWeb")
    assert key == "show s{season}e{ep}|s=2|e=1|f=mweb"
    assert key == level2_key("Show S02E01", 2, 1, "mweb")
    assert key != level2_key("Show S02E01", 2, 2, "MWeb")
    assert key != level2_key("Show S02E01", 2, 1, "LoliHouse")


def test_level2_key_renders_absent_components_as_dash() -> None:
    assert level2_key("Show", None, None, None) == "show|s=-|e=-|f=-"


def test_fansub_norm_folds_case_and_whitespace() -> None:
    assert fansub_norm("  MWeb ") == "mweb"
    assert fansub_norm("BeanSub & FZSD") == "beansub & fzsd"
    assert fansub_norm("   ") is None
    assert fansub_norm(None) is None


def test_key_level_constants_pin_the_two_levels() -> None:
    assert KEY_LEVEL_SERIES == 1
    assert KEY_LEVEL_EXACT == 2


def test_key_hash_is_stable_sha256() -> None:
    assert key_hash("abc") == hashlib.sha256(b"abc").hexdigest()
    assert key_hash("abc") == key_hash("abc")
    assert key_hash("abc") != key_hash("abd")
    assert stable_hash("abc") == key_hash("abc")


# --- placeholders -----------------------------------------------------------


def test_build_title_shape_replaces_marker_digits_with_placeholders() -> None:
    assert build_title_shape("Some.Title_S02E01") == "some title s{season}e{ep}"
    assert build_title_shape("作品 第2季 第01话") == "作品 第{season}季 第{ep}话"
    assert build_title_shape("Plain Title") == "plain title"
    assert build_title_shape("Score 9.5") == "score 9.5"


def test_backfill_title_fills_available_placeholders() -> None:
    shape = build_title_shape("Some.Title_S02E01")
    assert backfill_title(shape, season=2, episode=1) == "some title s2e1"
    assert backfill_title("plain", season=None, episode=None) == "plain"


def test_backfill_title_returns_none_when_a_placeholder_lacks_its_value() -> None:
    assert backfill_title("s{season}", season=None) is None
    assert backfill_title("e{ep}", episode=None) is None
    assert backfill_title("s{season}e{ep}", season=2, episode=None) is None


def test_placeholder_constants_are_the_documented_tokens() -> None:
    assert SEASON_PLACEHOLDER == "{season}"
    assert EPISODE_PLACEHOLDER == "{ep}"


# --- trust ------------------------------------------------------------------


def test_trust_score_is_hit_ratio() -> None:
    assert trust_score(3, 1) == 0.75
    assert trust_score(0, 5) == 0.0
    assert trust_score(5, 0) == 1.0


def test_trust_score_treats_zero_observations_as_trusted() -> None:
    # A freshly learned entry has never been corrected, so it may fuse.
    assert trust_score(0, 0) == 1.0


def test_trust_thresholds() -> None:
    assert should_demote_to_pending(0.49)
    assert not should_demote_to_pending(0.5)
    assert not can_fuse(0.79)
    assert can_fuse(0.8)
    assert TRUST_PENDING_THRESHOLD == 0.5
    assert TRUST_FUSION_THRESHOLD == 0.8


def test_fused_level_raises_only_medium_with_trusted_hit() -> None:
    assert fused_level(Confidence.MEDIUM, trusted_hit=True) is Confidence.HIGH
    assert fused_level(Confidence.MEDIUM, trusted_hit=False) is Confidence.MEDIUM
    assert fused_level(Confidence.HIGH, trusted_hit=True) is Confidence.HIGH
    assert fused_level(Confidence.LOW, trusted_hit=True) is Confidence.LOW


def test_eligible_for_memory_pins_the_routing_predicate() -> None:
    assert eligible_for_memory(Confidence.MEDIUM)
    assert not eligible_for_memory(Confidence.HIGH)
    assert not eligible_for_memory(Confidence.LOW)


# --- bypass -----------------------------------------------------------------


def test_pattern_hash_is_normalized_case_and_separator_insensitive() -> None:
    raw = "Some.Show.S01E01.Baha.WEB-DL.mkv"
    digest = pattern_hash(raw)
    assert digest == pattern_hash("some show s01e01 baha WEB-DL")
    assert digest == stable_hash(normalize_pattern(raw))


def test_is_bypassed_checks_digest_membership() -> None:
    digest = pattern_hash("Noisy Release [Group] - 01.mkv")
    assert is_bypassed(digest, [digest])
    assert is_bypassed(digest, {"other", digest})
    assert not is_bypassed(digest, [])
    assert not is_bypassed(pattern_hash("Another Show - 02"), [digest])


# --- draft ------------------------------------------------------------------


def test_memory_hit_from_stored_result_recovers_typed_fields() -> None:
    stored: dict[str, object] = {
        "title": "Anime AzurLane Slow Ahead",
        "season": 2,
        "episode": None,
        "segment": "season_pack",
        "fansub": "MWeb",
    }
    hit = MemoryHit.from_stored_result(stored, key_level=KEY_LEVEL_SERIES, trust=0.9)

    assert hit.key_level == 1
    assert hit.trust == 0.9
    assert hit.title == "Anime AzurLane Slow Ahead"
    assert hit.season == 2
    assert hit.episode is None
    assert hit.segment is Segment.SEASON_PACK
    assert hit.fansub == "MWeb"


def test_memory_hit_from_stored_result_tolerates_bad_entries() -> None:
    stored: dict[str, object] = {"title": "", "season": "2", "segment": "nope", "episode": True}
    hit = MemoryHit.from_stored_result(stored, key_level=KEY_LEVEL_EXACT, trust=0.0)

    assert hit.title is None
    assert hit.season is None
    assert hit.episode is None
    assert hit.segment is None
    assert hit.fansub is None


def test_apply_memory_hit_fills_missing_fields_and_upgrades_medium() -> None:
    hit = MemoryHit(key_level=KEY_LEVEL_SERIES, trust=1.0, season=1)
    enhanced = apply_memory_hit(_bleach_result(), hit)

    assert enhanced.season == 1
    assert enhanced.evidence["season"] == "memory"
    assert enhanced.evidence["key_level"] == "memory:1"
    assert enhanced.level is Confidence.HIGH
    assert enhanced.confidence == 1.0
    assert enhanced.missing_fields == ()
    assert enhanced.title == "BLEACH Sennen Kessen-hen"
    assert enhanced.episode == 42


def test_apply_memory_hit_untrusted_supplements_evidence_without_fusion() -> None:
    hit = MemoryHit(key_level=KEY_LEVEL_SERIES, trust=0.6, season=1)
    enhanced = apply_memory_hit(_bleach_result(), hit)

    assert enhanced.season == 1
    assert enhanced.evidence["season"] == "memory"
    assert enhanced.evidence["key_level"] == "memory:1"
    assert enhanced.level is Confidence.MEDIUM
    assert enhanced.confidence == 0.6


def test_apply_memory_hit_never_touches_name_or_folder_evidence() -> None:
    result = ParseResult(
        title="Some Show",
        season=None,
        episode=1,
        segment=Segment.EPISODE,
        fansub="L1Group",
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=("season",),
        evidence={"title": "name", "episode": "name", "segment": "name", "fansub": "folder"},
    )
    hit = MemoryHit(key_level=1, trust=1.0, season=1, fansub="MemoryGroup", title="Other Title")
    enhanced = apply_memory_hit(result, hit)

    assert enhanced.fansub == "L1Group"
    assert enhanced.evidence["fansub"] == "folder"
    assert enhanced.title == "Some Show"
    assert enhanced.season == 1
    assert enhanced.evidence["season"] == "memory"


def test_apply_memory_hit_without_fillable_fields_keeps_level() -> None:
    result = ParseResult(
        title="Some Show",
        season=2,
        episode=1,
        segment=Segment.EPISODE,
        fansub="Group",
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=(),
        evidence={
            "title": "name",
            "season": "name",
            "episode": "name",
            "segment": "name",
            "fansub": "name",
        },
    )
    hit = MemoryHit(key_level=2, trust=1.0, season=2)
    enhanced = apply_memory_hit(result, hit)

    assert enhanced.level is Confidence.MEDIUM
    assert enhanced.evidence["key_level"] == "memory:2"
    assert enhanced.season == 2


# --- protocols --------------------------------------------------------------


def test_memory_protocols_are_runtime_checkable() -> None:
    class _Store:
        async def find_parse_memory(self, key_level: int, key_hash: str) -> object | None:
            return None

        async def record_hit(self, parse_memory: object) -> None:
            return None

        async def record_correction(self, parse_memory: object) -> None:
            return None

        async def has_bypass(self, pattern_hash: str) -> bool:
            return False

    class _Recognizer:
        async def enhance(
            self, result: ParseResult, context: object, store: MemoryStore
        ) -> ParseResult | None:
            return None

    assert isinstance(_Store(), MemoryStore)
    assert isinstance(_Recognizer(), MemoryRecognizer)


# --- roundtrip fixtures -----------------------------------------------------


def test_load_roundtrip_cases_loads_examples_in_id_order() -> None:
    cases = load_roundtrip_cases()

    assert [case.id for case in cases] == ["R01_learn_then_lookup", "R02_learn_then_lookup"]


def test_load_roundtrip_case_r01_fields() -> None:
    case = load_roundtrip_case(ROUNDTRIP_ROOT / "R01_learn_then_lookup.json")

    assert case.learn.parse_result.level == "high"
    assert case.learn.confirmed.season == 2
    assert case.query.name == "Anime.AzurLane.Slow.Ahead.E03.1080p.Baha.WEB-DL.mkv"
    assert case.query.folder is None
    assert case.query.expected.season == 2
    assert case.query.expected.fansub == "MWeb"
    assert case.query.expected.evidence["season"] == "memory"
    assert case.query.expected.evidence["fansub"] == "memory"
    assert case.query.expected.evidence["key_level"] == "memory:1"


def test_load_roundtrip_case_r02_fields() -> None:
    case = load_roundtrip_case(ROUNDTRIP_ROOT / "R02_learn_then_lookup.json")

    assert case.learn.parse_result.level == "medium"
    assert case.learn.parse_result.missing_fields == ("season",)
    assert case.learn.confirmed.season == 1
    assert case.query.expected.season == 1
    assert case.query.expected.evidence["season"] == "memory"
    assert case.query.expected.evidence["key_level"] == "memory:1"


def test_roundtrip_loader_rejects_id_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "R99_case.json"
    path.write_text(json.dumps({"id": "R00_other", "learn": {}, "query": {}}), encoding="utf-8")

    with pytest.raises(FixtureError, match="id mismatch"):
        load_roundtrip_case(path)


def test_roundtrip_loader_rejects_missing_sections(tmp_path: Path) -> None:
    path = tmp_path / "R99_case.json"
    path.write_text(json.dumps({"id": "R99_case"}), encoding="utf-8")

    with pytest.raises(FixtureError, match="'learn' must be an object"):
        load_roundtrip_case(path)


def test_roundtrip_loader_rejects_unknown_evidence_source(tmp_path: Path) -> None:
    path = tmp_path / "R99_case.json"
    path.write_text(
        json.dumps(
            {
                "id": "R99_case",
                "learn": {"parse_result": _minimal_expected(), "confirmed": _minimal_expected()},
                "query": {"name": "Show - 01.mkv", "folder": None, "expected": _minimal_expected()},
            }
        ),
        encoding="utf-8",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["query"]["expected"]["evidence"]["season"] = "magic"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FixtureError, match="evidence\\[season\\]"):
        load_roundtrip_case(path)


def test_roundtrip_loader_rejects_bad_key_level_value(tmp_path: Path) -> None:
    path = tmp_path / "R99_case.json"
    path.write_text(
        json.dumps(
            {
                "id": "R99_case",
                "learn": {"parse_result": _minimal_expected(), "confirmed": _minimal_expected()},
                "query": {"name": "Show - 01.mkv", "folder": None, "expected": _minimal_expected()},
            }
        ),
        encoding="utf-8",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["query"]["expected"]["evidence"]["key_level"] = "memory:3"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FixtureError, match="memory:1' or 'memory:2"):
        load_roundtrip_case(path)


def _minimal_expected() -> dict[str, Any]:
    return {
        "title": "Show",
        "season": None,
        "episode": 1,
        "segment": "episode",
        "fansub": None,
        "level": "medium",
        "confidence": 0.6,
        "missing_fields": ["season"],
        "evidence": {"title": "name", "season": "none", "episode": "name"},
    }


# --- end-to-end roundtrip contract (pure functions + real L1, no DB) --------


@pytest.mark.parametrize("case", load_roundtrip_cases(), ids=lambda case: case.id)
async def test_roundtrip_fixture_holds_against_l1_and_l2_pure_functions(
    case: Any,
) -> None:
    """learn -> store -> query -> fuse, exercising exactly the fixture contract."""
    # Learning side: key and stored result come from the confirmed result.
    confirmed = case.learn.confirmed
    key = level1_key(confirmed.title)
    stored: dict[str, object] = {
        "title": confirmed.title,
        "season": confirmed.season,
        "episode": confirmed.episode,
        "segment": confirmed.segment,
        "fansub": confirmed.fansub,
    }

    # Query side: real L1 parse of the raw query name, then a memory hit.
    l1_result = await LocalRecognizer().parse(
        RawName(name=case.query.name, folder=case.query.folder, parent_path="Z:/Downloads")
    )
    assert l1_result is not None
    assert eligible_for_memory(l1_result.level)
    assert key  # the level-1 key derived from the confirmed title is non-empty

    hit = MemoryHit.from_stored_result(stored, key_level=KEY_LEVEL_SERIES, trust=trust_score(0, 0))
    enhanced = apply_memory_hit(l1_result, hit)

    expected = case.query.expected
    assert enhanced.title == expected.title
    assert enhanced.season == expected.season
    assert enhanced.episode == expected.episode
    assert enhanced.segment.value == expected.segment
    assert enhanced.fansub == expected.fansub
    assert enhanced.level.value == expected.level
    assert enhanced.confidence == expected.confidence
    assert list(enhanced.missing_fields) == list(expected.missing_fields)
    assert enhanced.evidence == expected.evidence
