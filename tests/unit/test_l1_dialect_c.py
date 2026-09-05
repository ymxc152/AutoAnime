"""Dialect C (pure bracket flow): fixture parity and boundary tests."""

from __future__ import annotations

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, RawName
from autoanime.pipeline.l1.dialects import pure_bracket
from tests.support.fixtures import FixtureCase, load_dialects

CASES = load_dialects("c")


def _parse_case(case: FixtureCase):
    assert len(case.files) == 1, "dialect C fixtures are single-file"
    return pure_bracket.parse(case.to_raw_names()[0])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_fixture_matches_expected(case: FixtureCase) -> None:
    result = _parse_case(case)
    expected = case.expected
    assert expected is not None, f"missing expected.json for {case.id}"
    assert result is not None
    assert result.title == expected.title
    assert result.season == expected.season
    assert result.episode == expected.episode
    assert result.segment.value == expected.segment
    assert result.fansub == expected.fansub
    assert result.level.value == expected.level
    assert result.confidence == expected.confidence
    assert result.missing_fields == expected.missing_fields
    assert result.evidence == expected.evidence


def test_technical_brackets_are_never_fansub_or_title() -> None:
    result = pure_bracket.parse(
        RawName(name="[1920x1080][AVC_AAC][CHT][Show][05][HEVC].mp4")
    )
    assert result is not None
    assert result.fansub is None
    assert result.title == "Show"
    assert result.episode == 5
    assert result.evidence["fansub"] == "none"
    assert result.level is Confidence.MEDIUM


def test_resolution_without_p_suffix_is_not_an_anchor_for_title() -> None:
    # 1920x1080 (no "p") must not leak into the title nor break the bracket flow.
    result = pure_bracket.parse(
        RawName(name="[64bitsub][Show][11][3840x2160][HEVC][CHS].mkv")
    )
    assert result is not None
    assert result.title == "Show"
    assert result.episode == 11
    assert result.fansub == "64bitsub"


def test_no_season_marker_means_season_stays_absent() -> None:
    result = pure_bracket.parse(RawName(name="[64bitsub][Show][02][1920x1080][AVC_AAC].mp4"))
    assert result is not None
    assert result.season is None
    assert result.segment is Segment.EPISODE
    assert result.missing_fields == ("season",)


def test_episode_bracket_absent_keeps_missing_fields() -> None:
    result = pure_bracket.parse(RawName(name="[64bitsub][Show][AVC_AAC][CHT].mp4"))
    assert result is not None
    assert result.episode is None
    assert result.missing_fields == ("season", "episode")
    assert result.evidence["episode"] == "none"
    assert result.level is Confidence.MEDIUM


def test_episode_beyond_release_progress_drops_to_low() -> None:
    case = next(case for case in CASES if case.id == "C01_pure_bracket_flow")
    result = pure_bracket.parse(case.to_raw_names()[0], ParseContext(release_progress=5))
    assert result is not None
    assert result.episode == 8
    assert result.level is Confidence.LOW
    assert result.confidence == 0.2
    assert result.evidence["release_progress"] == "context"


def test_names_without_bracket_flow_return_none() -> None:
    assert pure_bracket.parse(RawName(name="Show - 08.mp4")) is None
    assert pure_bracket.parse(RawName(name="")) is None
