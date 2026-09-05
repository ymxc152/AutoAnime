"""Dialect A (dot-separated MWeb names): fixture parity and boundary tests."""

from __future__ import annotations

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, RawName
from autoanime.pipeline.l1.dialects import dot
from tests.support.fixtures import FixtureCase, load_dialects

CASES = load_dialects("a")


def _parse_case(case: FixtureCase):
    assert len(case.files) == 1, "dialect A fixtures are single-file"
    return dot.parse(case.to_raw_names()[0])


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


def test_mweb_season_pack_is_high_confidence() -> None:
    result = dot.parse(
        RawName(
            name="Anime.Show.S02.1080p.Baha.WEB-DL.AAC2.0.H.264-MWeb",
            folder="Anime.Show.S02.1080p.Baha.WEB-DL.AAC2.0.H.264-MWeb",
        )
    )
    assert result is not None
    assert result.segment is Segment.SEASON_PACK
    assert result.level is Confidence.HIGH
    assert result.confidence == 1.0
    assert result.missing_fields == ()


def test_linetv_source_is_anchor_not_title() -> None:
    result = dot.parse(
        RawName(name="Some.Title.S01E05.1080p.LINETV.WEB-DL.AAC2.0.H.264-MWeb.mkv")
    )
    assert result is not None
    assert result.season == 1
    assert result.episode == 5
    assert "LINETV" not in result.title
    assert result.fansub == "MWeb"
    assert result.level is Confidence.HIGH


def test_single_letter_tokens_are_word_internal_dots() -> None:
    result = dot.parse(
        RawName(name="E.X.Tanaka.S01E01.1080p.Baha.WEB-DL.AAC2.0.H.264-MWeb.mkv")
    )
    assert result is not None
    assert result.title == "E X Tanaka"
    assert result.level is Confidence.MEDIUM


def test_camel_case_token_is_not_word_internal() -> None:
    result = dot.parse(
        RawName(name="Azur.Lane.Bisoku.Go.S01E01.1080p.Baha.WEB-DL-MWeb.mkv")
    )
    assert result is not None
    assert result.title == "Azur Lane Bisoku Go"
    assert result.level is Confidence.HIGH


def test_all_upper_title_with_multiple_tokens_downgrades() -> None:
    result = dot.parse(RawName(name="SOLO.LEVELING.S01E01.1080p.Baha.WEB-DL-MWeb.mkv"))
    assert result is not None
    assert result.title == "SOLO LEVELING"
    assert result.level is Confidence.MEDIUM


def test_single_upper_token_is_not_ambiguous() -> None:
    result = dot.parse(RawName(name="HISTORY.S01E01.1080p.Baha.WEB-DL-MWeb.mkv"))
    assert result is not None
    assert result.title == "HISTORY"
    assert result.level is Confidence.HIGH


def test_folder_fills_missing_season() -> None:
    result = dot.parse(
        RawName(
            name="Some.Title.1080p.Baha.WEB-DL-MWeb.mkv",
            folder="Some.Title.S02.1080p.Baha.WEB-DL-MWeb",
        )
    )
    assert result is not None
    assert result.season == 2
    assert result.segment is Segment.SEASON_PACK
    assert result.evidence["season"] == "folder"
    assert result.evidence["segment"] == "folder"
    assert result.missing_fields == ()
    assert result.level is Confidence.MEDIUM


def test_folder_conflict_prefers_filename_and_downgrades() -> None:
    result = dot.parse(
        RawName(
            name="Some.Title.S01E01.1080p.Baha.WEB-DL-MWeb.mkv",
            folder="Some.Title.S03.1080p.Baha.WEB-DL-MWeb",
        )
    )
    assert result is not None
    assert result.season == 1
    assert result.episode == 1
    assert result.evidence["season"] == "name"
    assert result.level is Confidence.MEDIUM


def test_episode_beyond_release_progress_drops_to_low() -> None:
    result = dot.parse(
        RawName(
            name="Please.Excuse.My.Younger.Brothers.S01E02.1080p.friDay.WEB-DL.AAC2.0.H.264-MWeb.mkv"
        ),
        ParseContext(release_progress=1),
    )
    assert result is not None
    assert result.episode == 2
    assert result.level is Confidence.LOW
    assert result.confidence == 0.2
    assert result.evidence["release_progress"] == "context"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Just Some Random Text.mkv",
        "random_text_only",
        # Regression: pure-bracket / batch names used to raise ValueError from
        # to_parse_result (segment unset) instead of returning None.
        "[Nekomoe kissaten&LoliHouse] Akane-banashi [03-06][WebRip 1080p HEVC-10bit AAC ASSx2]",
        "[64bitsub][Haibara-kun no Tsuyokute Seishun New Game][08][1920x1080][AVC_AAC][CHT].mp4",
    ],
)
def test_unrecognizable_names_return_none(name: str) -> None:
    assert dot.parse(RawName(name=name)) is None


def test_folder_rescues_segmentless_name() -> None:
    """The segment guard runs after the folder merge, not before it."""
    result = dot.parse(
        RawName(
            name="Some.Title.1080p.WEB-DL.AAC2.0.H.264-MWeb",
            folder="Some.Title.S02.1080p.Baha.WEB-DL.AAC2.0.H.264-MWeb",
        )
    )
    assert result is not None
    assert result.segment is Segment.SEASON_PACK
    assert result.season == 2
    assert result.evidence["season"] == "folder"


def test_folder_equal_to_name_is_not_reparsed() -> None:
    folder = "Anime.Show.S02.1080p.Baha.WEB-DL-MWeb"
    result = dot.parse(RawName(name=folder, folder=folder))
    assert result is not None
    assert all(source in ("name", "none") for source in result.evidence.values())
