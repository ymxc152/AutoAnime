"""Dialect B (bracket-prefixed names): fixture parity and boundary tests."""

from __future__ import annotations

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, RawName
from autoanime.pipeline.l1.dialects import bracket
from tests.support.fixtures import FixtureCase, load_dialects

CASES = load_dialects("b")


def _parse_case(case: FixtureCase):
    assert len(case.files) == 1, "dialect B fixtures are single-file"
    return bracket.parse(case.to_raw_names()[0])


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


def test_multi_fansub_cosing_is_kept_verbatim() -> None:
    result = bracket.parse(
        RawName(name="[BeanSub&FZSD&LoliHouse] Show - 07 [WebRip 1080p HEVC-10bit].mkv")
    )
    assert result is not None
    assert result.fansub == "BeanSub&FZSD&LoliHouse"
    assert result.episode == 7
    assert result.title == "Show"


def test_double_episode_reports_season_relative_number() -> None:
    result = bracket.parse(RawName(name="[BeanSub&LoliHouse] Show 3rd Season - 12(86)"))
    assert result is not None
    assert result.season == 3
    assert result.episode == 12
    assert result.title == "Show"
    assert result.level is Confidence.HIGH
    assert result.missing_fields == ()


def test_batch_range_reports_range_start() -> None:
    result = bracket.parse(
        RawName(name="[Nekomoe kissaten] Show [10-12][WebRip 1080p HEVC-10bit]")
    )
    assert result is not None
    assert result.episode == 10
    assert result.segment is Segment.EPISODE
    assert result.missing_fields == ("season",)
    assert result.level is Confidence.MEDIUM


def test_explicit_season_marker_is_stripped_from_title() -> None:
    result = bracket.parse(RawName(name="[Sub] Show S3 - 07 [WebRip 1080p].mkv"))
    assert result is not None
    assert result.season == 3
    assert result.episode == 7
    assert result.title == "Show"
    assert result.level is Confidence.HIGH


def test_technical_first_bracket_yields_no_fansub() -> None:
    result = bracket.parse(RawName(name="[WebRip 1080p] Show - 01.mkv"))
    assert result is not None
    assert result.fansub is None
    assert result.episode == 1
    assert result.title == "Show"
    assert result.evidence["fansub"] == "none"


def test_episode_beyond_release_progress_drops_to_low() -> None:
    case = next(case for case in CASES if case.id == "B05_end_marker")
    result = bracket.parse(case.to_raw_names()[0], ParseContext(release_progress=12))
    assert result is not None
    assert result.episode == 13
    assert result.level is Confidence.LOW
    assert result.confidence == 0.2
    assert result.evidence["release_progress"] == "context"


@pytest.mark.parametrize(
    "name",
    [
        "Show - 01.mkv",  # no bracket structure at all
        "[Sub] Show [WebRip 1080p].mkv",  # brackets but no season/episode
    ],
)
def test_unrecognizable_names_return_none(name: str) -> None:
    assert bracket.parse(RawName(name=name)) is None


def test_season_hidden_in_title_stays_absent() -> None:
    # "Sennen Kessen-hen" is an arc name, not a season number: the draft keeps
    # season missing instead of guessing.
    case = next(case for case in CASES if case.id == "B01_multifansub_season_hidden_in_title")
    result = _parse_case(case)
    assert result is not None
    assert result.season is None
    assert result.missing_fields == ("season",)
