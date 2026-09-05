"""Unit tests for the shared L1 infrastructure modules."""

from __future__ import annotations

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult
from autoanime.pipeline.l1 import (
    SOURCE_FOLDER,
    SOURCE_NAME,
    SOURCE_NONE,
    AnchorKind,
    L1Draft,
    anchor_free_chunks,
    apply_release_progress,
    base_level,
    choose_prefer_name,
    clean_noise,
    confidence_for,
    detect_segment,
    downgrade,
    extract_episode,
    extract_episode_numbers,
    extract_fansub,
    extract_season,
    find_anchors,
    find_anchors_of_kind,
    is_likely_fansub,
    merge_folder_draft,
    merge_levels,
    missing_fields_for,
    normalize_name,
    normalize_whitespace,
    parse_with_anitopy,
    separators_to_spaces,
    strip_extension,
)
from autoanime.pipeline.l1.draft import L1ContractError

BEANSUB_NAME = "[BeanSub] BLEACH Sennen Kessen-hen - 41 [WebRip 1080p HEVC-10bit AAC ASSx2].mkv"
DIALECT_A_NAME = "Some.Title.S02E01.1080p.Baha.WEB-DL.mkv"
DIALECT_A_FOLDER = "Anime.Some.Title.S02.1080p.Baha.WEB-DL-MWeb"


def test_strip_extension_removes_known_media_extensions() -> None:
    assert strip_extension("Show S01E01.mkv") == "Show S01E01"
    assert strip_extension("Show S01E01.ASS") == "Show S01E01"
    assert strip_extension("Batch.S01.S02.unknownxyz") == "Batch.S01.S02.unknownxyz"


def test_normalize_whitespace_folds_nbsp_and_fullwidth_space() -> None:
    assert normalize_whitespace("  A\u3000 B \t C ") == "A B C"


def test_separators_to_spaces_keeps_decimals() -> None:
    assert separators_to_spaces("Some.Title_S02") == "Some Title S02"
    assert separators_to_spaces("Score.9.5") == "Score 9.5"


def test_clean_noise_removes_recruitment_brackets_and_urls() -> None:
    noisy = "[风车字幕组][招募后期] Title [www.example.com]"
    assert clean_noise(noisy) == "[风车字幕组] Title"


def test_normalize_name_strips_extension_and_noise() -> None:
    assert normalize_name("Title S01E01.mkv") == "Title S01E01"


def test_find_anchors_detects_all_kinds() -> None:
    anchors = find_anchors(DIALECT_A_NAME)
    kinds = {anchor.kind for anchor in anchors}

    assert kinds == {AnchorKind.SEASON, AnchorKind.EPISODE, AnchorKind.RESOLUTION, AnchorKind.SOURCE}
    seasons = find_anchors_of_kind(DIALECT_A_NAME, AnchorKind.SEASON)
    assert seasons[0].text == "S02"


def test_find_anchors_detects_chinese_markers_and_brackets() -> None:
    assert find_anchors_of_kind("某作品 第2季", AnchorKind.SEASON)[0].text == "第2季"
    assert find_anchors_of_kind("某作品 第12话", AnchorKind.EPISODE)[0].text == "第12话"
    assert find_anchors_of_kind(BEANSUB_NAME, AnchorKind.BRACKET)[0].text == "[BeanSub]"


def test_anchor_free_chunks_masks_anchors_and_keeps_dots() -> None:
    chunks = anchor_free_chunks(DIALECT_A_NAME)
    assert chunks == ["Some.Title"]


def test_anchor_free_chunks_on_bracket_flow() -> None:
    chunks = anchor_free_chunks(BEANSUB_NAME)
    assert chunks == ["BLEACH Sennen Kessen-hen"]


def test_extract_season_and_episode() -> None:
    assert extract_season(DIALECT_A_NAME) == 2
    assert extract_episode(DIALECT_A_NAME) == 1
    assert extract_season(BEANSUB_NAME) is None
    assert extract_episode(BEANSUB_NAME) == 41


def test_extract_episode_numbers_returns_all_markers() -> None:
    assert extract_episode_numbers("Show E01-E03.mkv") == [1, 3]
    assert extract_episode_numbers("Show 第12话") == [12]


def test_extract_fansub_from_bracket_and_trailing_group() -> None:
    assert extract_fansub(BEANSUB_NAME) == "BeanSub"
    assert extract_fansub(DIALECT_A_FOLDER) == "MWeb"
    assert extract_fansub(DIALECT_A_NAME) is None


def test_is_likely_fansub_rejects_technical_tokens() -> None:
    assert is_likely_fansub("BeanSub")
    assert not is_likely_fansub("1080p")
    assert not is_likely_fansub("WEB-DL")
    assert not is_likely_fansub("123")


def test_detect_segment_rules() -> None:
    assert detect_segment("剧场版 Title") == Segment.MOVIE
    assert detect_segment("Title S02E01", season=2, episode=1) == Segment.EPISODE
    assert detect_segment("Title S02", season=2) == Segment.SEASON_PACK
    assert detect_segment("Title", season=None, episode=None) is None


def test_confidence_mapping_and_downgrade() -> None:
    assert confidence_for(Confidence.HIGH) == 1.0
    assert confidence_for(Confidence.MEDIUM) == 0.6
    assert confidence_for(Confidence.LOW) == 0.2
    assert downgrade(Confidence.HIGH) == Confidence.MEDIUM
    assert downgrade(Confidence.MEDIUM, steps=2) == Confidence.LOW
    assert downgrade(Confidence.LOW) == Confidence.LOW
    assert merge_levels(Confidence.HIGH, Confidence.LOW) == Confidence.LOW


def test_missing_fields_depends_on_segment() -> None:
    assert missing_fields_for(title="T", season=2, episode=1, segment=Segment.EPISODE) == ()
    assert missing_fields_for(title="T", season=None, episode=1, segment=Segment.EPISODE) == ("season",)
    assert missing_fields_for(title="T", season=2, episode=None, segment=Segment.SEASON_PACK) == ()
    assert missing_fields_for(title="T", season=None, episode=None, segment=Segment.MOVIE) == ()
    assert missing_fields_for(title=None, season=None, episode=None, segment=None) == (
        "title",
        "season",
        "episode",
    )


def test_base_level_completeness_only() -> None:
    assert base_level(title="T", season=2, episode=1, segment=Segment.EPISODE) == Confidence.HIGH
    assert base_level(title="T", season=None, episode=1, segment=Segment.EPISODE) == Confidence.MEDIUM
    assert base_level(title="", season=None, episode=None, segment=None) == Confidence.LOW


def test_finalize_clamps_high_with_missing_fields() -> None:
    draft = L1Draft(title="T", season=2, episode=None, segment=Segment.EPISODE, level=Confidence.HIGH)
    finished = draft.finalized()

    assert finished.level == Confidence.MEDIUM
    assert finished.missing_fields == ("episode",)


def test_finalize_preserves_explicit_low_downgrade() -> None:
    draft = L1Draft(title="T", season=2, episode=1, segment=Segment.EPISODE, level=Confidence.LOW)
    assert draft.finalized().level == Confidence.LOW


def test_l1_draft_confidence_and_to_parse_result() -> None:
    draft = L1Draft(
        title="Some Title",
        season=2,
        episode=1,
        segment=Segment.EPISODE,
        fansub="MWeb",
        level=Confidence.HIGH,
        evidence={"title": SOURCE_NAME, "fansub": SOURCE_FOLDER},
    )
    result = draft.to_parse_result()

    assert isinstance(result, ParseResult)
    assert result.title == "Some Title"
    assert result.season == 2
    assert result.episode == 1
    assert result.segment == Segment.EPISODE
    assert result.fansub == "MWeb"
    assert result.level == Confidence.HIGH
    assert result.confidence == 1.0
    assert result.evidence["fansub"] == SOURCE_FOLDER


def test_l1_draft_requires_segment_for_parse_result() -> None:
    with pytest.raises(L1ContractError, match="segment"):
        L1Draft(title="T").to_parse_result()


def test_choose_prefer_name_records_source() -> None:
    assert choose_prefer_name(2, 3) == (2, SOURCE_NAME)
    assert choose_prefer_name(None, 3) == (3, SOURCE_FOLDER)
    assert choose_prefer_name(None, None) == (None, SOURCE_NONE)


def test_merge_folder_draft_fills_missing_fields_from_folder() -> None:
    name_draft = L1Draft(title="Some Title", episode=1, segment=Segment.EPISODE)
    folder_draft = L1Draft(title="Anime Some Title", season=2, fansub="MWeb")
    merged = merge_folder_draft(name_draft, folder_draft)

    assert merged.title == "Some Title"
    assert merged.season == 2
    assert merged.fansub == "MWeb"
    assert merged.evidence["title"] == SOURCE_NAME
    assert merged.evidence["season"] == SOURCE_FOLDER
    assert merged.evidence["episode"] == SOURCE_NAME


def test_merge_folder_draft_conflict_prefers_name_and_downgrades() -> None:
    name_draft = L1Draft(
        title="Some Title",
        season=2,
        episode=1,
        segment=Segment.EPISODE,
        level=Confidence.HIGH,
    )
    folder_draft = L1Draft(title="Other Title", season=3, episode=9, level=Confidence.HIGH)
    merged = merge_folder_draft(name_draft, folder_draft)

    assert merged.season == 2
    assert merged.episode == 1
    assert merged.title == "Some Title"
    assert merged.level == Confidence.MEDIUM


def test_merge_folder_draft_with_none_returns_name_draft() -> None:
    name_draft = L1Draft(title="T", season=1, episode=2, segment=Segment.EPISODE)
    assert merge_folder_draft(name_draft, None) == name_draft


def test_apply_release_progress_downgrades_to_low() -> None:
    draft = L1Draft(title="T", season=2, episode=25, segment=Segment.EPISODE, level=Confidence.HIGH)

    assert apply_release_progress(draft, ParseContext(release_progress=24)).level == Confidence.LOW
    assert apply_release_progress(draft, ParseContext(release_progress=25)) is draft
    assert apply_release_progress(draft, None) is draft


def test_parse_with_anitopy_extracts_core_fields() -> None:
    parsed = parse_with_anitopy(BEANSUB_NAME)

    assert parsed["anime_title"] == "BLEACH Sennen Kessen-hen"
    assert parsed["episode_number"] == "41"
    assert parsed["release_group"] == "BeanSub"
    assert parsed["video_resolution"] == "1080p"


def test_parse_with_anitopy_returns_empty_on_garbage() -> None:
    assert parse_with_anitopy("") == {}
