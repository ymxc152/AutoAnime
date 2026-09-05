"""Unit tests for the L1 dialect E recognizer (ANi-style episode names)."""

from __future__ import annotations

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.dialects import parse_ep
from tests.support.fixtures import FixtureCase, FixtureExpected, load_dialects

E_CASES = load_dialects("e")


def _parse(
    name: str, folder: str | None = None, context: ParseContext | None = None
) -> ParseResult | None:
    return parse_ep(RawName(name=name, folder=folder, parent_path="Z:/Downloads"), context)


def _assert_expected(result: ParseResult | None, expected: FixtureExpected) -> None:
    assert result is not None
    assert result.title == expected.title
    assert result.season == expected.season
    assert result.episode == expected.episode
    assert result.segment == Segment(expected.segment)
    assert result.fansub == expected.fansub
    assert result.level == Confidence(expected.level)
    assert result.confidence == expected.confidence
    assert result.missing_fields == expected.missing_fields
    assert result.evidence == expected.evidence


def test_every_dialect_e_fixture_has_expected() -> None:
    assert E_CASES
    assert all(case.expected is not None for case in E_CASES)


@pytest.mark.parametrize("case", E_CASES, ids=lambda case: case.id)
def test_dialect_e_fixtures_match_expected(case: FixtureCase) -> None:
    assert case.expected is not None
    for raw in case.to_raw_names():
        _assert_expected(parse_ep(raw), case.expected)


@pytest.mark.parametrize(
    ("marker", "expected_episode"),
    [
        ("- 03", 3),
        ("EP03", 3),
        ("E03", 3),
    ],
)
def test_episode_forms_cover_dash_and_ep_prefixes(marker: str, expected_episode: int) -> None:
    result = _parse(f"[ANi] 標題 {marker} [1080P][Baha][WEB-DL]")
    assert result is not None
    assert result.episode == expected_episode
    assert result.segment is Segment.EPISODE


@pytest.mark.parametrize(
    ("word", "expected_season"),
    [
        ("1st Season", 1),
        ("2nd Season", 2),
        ("3rd Season", 3),
    ],
)
def test_ordinal_season_words(word: str, expected_season: int) -> None:
    result = _parse(f"[ANi] 標題 {word} - 05 [1080P][Baha][WEB-DL]")
    assert result is not None
    assert result.season == expected_season
    assert word not in result.title


def test_exclamation_season_hiding_in_title_fullwidth() -> None:
    result = _parse("[ANi] 碧藍航線 微速前進！2！！ - 02 [1080P][Baha][WEB-DL]")
    assert result is not None
    assert result.title == "碧藍航線 微速前進！"
    assert result.season == 2


def test_exclamation_season_hiding_in_title_halfwidth() -> None:
    result = _parse("[ANi] 標題!2!! - 05 [1080P][Baha][WEB-DL]")
    assert result is not None
    assert result.title == "標題!"
    assert result.season == 2


@pytest.mark.parametrize(
    "name",
    [
        "[BeanSub] 標題 - 02 [1080P][Baha][WEB-DL]",
        "標題 - 02 [1080P][Baha][WEB-DL]",
        "[ANiRaws] 標題 - 02 [1080P]",
    ],
)
def test_non_ani_prefix_returns_none(name: str) -> None:
    assert _parse(name) is None


def test_name_without_episode_or_season_returns_none() -> None:
    assert _parse("[ANi] 標題 [1080P][Baha][WEB-DL]") is None


def test_baha_source_is_never_the_fansub() -> None:
    result = _parse("[ANi] 標題 - 02 [1080P][Baha][WEB-DL][AAC AVC][CHT]")
    assert result is not None
    assert result.fansub == "ANi"


def test_fansub_pref_does_not_rewrite_parsed_group() -> None:
    result = _parse(
        "[ANi] 標題 - 02 [1080P][Baha][WEB-DL]", context=ParseContext(fansub_pref="某字幕组")
    )
    assert result is not None
    assert result.fansub == "ANi"


def test_missing_season_is_reported_and_caps_at_medium() -> None:
    result = _parse("[ANi] 標題 - 02 [1080P][Baha][WEB-DL]")
    assert result is not None
    assert result.season is None
    assert result.missing_fields == ("season",)
    assert result.level is Confidence.MEDIUM


def test_folder_season_fills_missing_season() -> None:
    result = _parse("[ANi] 標題 - 03 [1080P][Baha][WEB-DL]", folder="第二季")
    assert result is not None
    assert result.season == 2
    assert result.evidence["season"] == "folder"
    assert result.level is Confidence.HIGH


def test_folder_season_conflict_prefers_name_and_downgrades() -> None:
    result = _parse("[ANi] 標題 2nd Season - 03 [1080P][Baha][WEB-DL]", folder="第三季")
    assert result is not None
    assert result.season == 2
    assert result.evidence["season"] == "name"
    assert result.level is Confidence.MEDIUM


def test_conflicting_season_markers_inside_name_drop_to_low() -> None:
    result = _parse("[ANi] 標題 2nd Season S03 - 03 [1080P][Baha][WEB-DL]")
    assert result is not None
    assert result.season == 2
    assert result.title == "標題"
    assert result.level is Confidence.LOW


def test_episode_beyond_release_progress_drops_to_low() -> None:
    result = _parse(
        "[ANi] 標題 2nd Season - 14 [1080P][Baha][WEB-DL]",
        context=ParseContext(release_progress=10),
    )
    assert result is not None
    assert result.level is Confidence.LOW
    assert result.episode == 14
