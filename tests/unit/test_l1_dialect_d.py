"""Unit tests for the L1 dialect D recognizer (CJK subtitle-group names)."""

from __future__ import annotations

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.dialects import parse_cjk
from tests.support.fixtures import FixtureCase, FixtureExpected, load_dialects

D_CASES = load_dialects("d")


def _parse(
    name: str, folder: str | None = None, context: ParseContext | None = None
) -> ParseResult | None:
    return parse_cjk(RawName(name=name, folder=folder, parent_path="Z:/Downloads"), context)


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


def test_every_dialect_d_fixture_has_expected() -> None:
    assert D_CASES
    assert all(case.expected is not None for case in D_CASES)


@pytest.mark.parametrize("case", D_CASES, ids=lambda case: case.id)
def test_dialect_d_fixtures_match_expected(case: FixtureCase) -> None:
    assert case.expected is not None
    for raw in case.to_raw_names():
        _assert_expected(parse_cjk(raw), case.expected)


@pytest.mark.parametrize(
    ("word", "expected_season"),
    [
        ("第一季", 1),
        ("第二季", 2),
        ("第三季", 3),
        ("第十二季", 12),
        ("第2季", 2),
    ],
)
def test_chinese_season_words_cover_numerals_and_digits(
    word: str, expected_season: int
) -> None:
    result = _parse(f"[某字幕组]某作品 {word} [05]")
    assert result is not None
    assert result.season == expected_season
    assert result.evidence["season"] == "name"


def test_agreeing_season_markers_keep_high_structure() -> None:
    result = _parse("[某字幕组]某作品 第二季 Sousou no Frieren S2 [05]")
    assert result is not None
    assert result.season == 2
    assert result.level is Confidence.MEDIUM


def test_conflicting_season_markers_inside_name_drop_to_low() -> None:
    result = _parse("[某字幕组]某作品 第三季 S2 [05]")
    assert result is not None
    assert result.level is Confidence.LOW
    assert result.confidence == 0.2
    assert result.season == 3


def test_trailing_recruit_noise_is_dropped_from_title() -> None:
    result = _parse("[某字幕组]某作品 第二季 [05][简体][1080P]招募翻译")
    assert result is not None
    assert result.title == "某作品"
    assert "招募" not in result.title


def test_bilingual_title_keeps_chinese_and_romanized_parts() -> None:
    result = _parse("[某字幕组]某作品 第二季 Sousou no Frieren S2 [05]")
    assert result is not None
    assert result.title == "某作品 Sousou no Frieren"


def test_resolution_bracket_is_not_an_episode_and_yields_season_pack() -> None:
    result = _parse("[某字幕组]某作品 第二季 [1080P] Complete")
    assert result is not None
    assert result.episode is None
    assert result.season == 2
    assert result.segment is Segment.SEASON_PACK


def test_year_bracket_is_not_an_episode() -> None:
    result = _parse("[某字幕组]某作品 第二季 [2024]")
    assert result is not None
    assert result.episode is None
    assert result.segment is Segment.SEASON_PACK


def test_end_bracket_does_not_break_the_episode() -> None:
    result = _parse("[某字幕组]某作品 第二季 [10][END][1080P]")
    assert result is not None
    assert result.episode == 10
    assert result.segment is Segment.EPISODE


def test_name_without_any_cjk_returns_none() -> None:
    assert _parse("Some.Title.S02E01.1080p.Baha.WEB-DL.mkv") is None


def test_ani_style_name_belongs_to_the_ep_dialect() -> None:
    assert _parse("[ANi] 標題 - 02 [1080P][Baha][WEB-DL].mp4") is None


def test_folder_fills_the_chinese_title_and_season() -> None:
    result = _parse(
        "Show.Title.S02.Complete.1080p.CR.WEB-DL.mkv",
        folder="[某作品 第二季].Show.Title.S02.Complete.1080p.CR.WEB-DL",
    )
    assert result is not None
    assert result.title == "某作品"
    assert result.evidence["title"] == "folder"
    assert result.season == 2
    assert result.evidence["season"] == "name"
    assert result.segment is Segment.SEASON_PACK
    assert result.level is Confidence.HIGH


def test_folder_season_fills_missing_name_season() -> None:
    result = _parse("Show.Title.Complete.1080p.WEB-DL.mkv", folder="[某作品 第二季]")
    assert result is not None
    assert result.season == 2
    assert result.evidence["season"] == "folder"
    assert result.level is Confidence.HIGH


def test_folder_season_conflict_prefers_name_and_downgrades() -> None:
    result = _parse("[某字幕组]某作品 第二季 [05]", folder="[某作品 第三季]")
    assert result is not None
    assert result.season == 2
    assert result.evidence["season"] == "name"
    assert result.level is Confidence.MEDIUM
    assert result.confidence == 0.6


def test_latin_reconstruction_from_word_internal_dots_caps_at_medium() -> None:
    result = _parse("Some.Title.2023.S02.Complete.1080p.WEB-DL.mkv", folder="[简体][1080P]")
    assert result is not None
    assert result.title == "Some Title"
    assert result.evidence["title"] == "name"
    assert result.segment is Segment.SEASON_PACK
    assert result.level is Confidence.MEDIUM


def test_episode_beyond_release_progress_drops_to_low() -> None:
    result = _parse("[某字幕组]某作品 第二季 [10]", context=ParseContext(release_progress=5))
    assert result is not None
    assert result.level is Confidence.LOW


def test_fansub_pref_does_not_rewrite_parsed_group() -> None:
    result = _parse(
        "[云光字幕组]某作品 第二季 [05]", context=ParseContext(fansub_pref="别家字幕组")
    )
    assert result is not None
    assert result.fansub == "云光字幕组"
