"""Dialect F (special) unit tests: fixture consistency and boundary values."""

from __future__ import annotations

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.dialects.special import parse_special
from tests.support.fixtures import FixtureCase, load_dialects

_EVIDENCE_FIELDS = frozenset({"title", "season", "episode", "segment", "fansub"})
_EVIDENCE_SOURCES = frozenset({"name", "folder", "context", "none"})


def _parse(
    name: str, folder: str | None = None, context: ParseContext | None = None
) -> ParseResult | None:
    return parse_special(RawName(name=name, folder=folder, parent_path="Z:/Downloads"), context)


def _assert_evidence_contract(result: ParseResult) -> None:
    assert set(result.evidence) == _EVIDENCE_FIELDS
    assert set(result.evidence.values()) <= _EVIDENCE_SOURCES
    assert result.confidence == {Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.6, Confidence.LOW: 0.2}[
        result.level
    ]


@pytest.mark.parametrize("case", load_dialects("F"), ids=lambda case: case.id)
def test_fixture_expected_matches_parse(case: FixtureCase) -> None:
    assert case.expected is not None
    expected = case.expected
    for index, raw in enumerate(case.to_raw_names()):
        result = _parse(raw.name, raw.folder)
        assert result is not None
        if index == 0:
            assert result.episode == expected.episode
        else:
            assert result.episode is not None
        assert result.title == expected.title
        assert result.season == expected.season
        assert result.segment.value == expected.segment
        assert result.fansub == expected.fansub
        assert result.level.value == expected.level
        assert result.confidence == expected.confidence
        assert result.missing_fields == expected.missing_fields
        assert result.evidence == expected.evidence
        _assert_evidence_contract(result)


@pytest.mark.parametrize(
    ("name", "folder", "expected_segment", "expected_season", "expected_episode", "expected_level"),
    [
        # 剧场版 marker without a year still resolves to MOVIE.
        ("[7³ACG] 剧场版 That Title.mkv", None, Segment.MOVIE, None, None, Confidence.HIGH),
        # A standalone year implies a movie even without an explicit marker.
        ("Some Movie 2019 [G].mkv", None, Segment.MOVIE, None, None, Confidence.HIGH),
        # Season conflict: filename wins (S02), merged result is downgraded.
        ("Show S02E05 [Sub].mkv", "Show 第3季 [Sub]", Segment.EPISODE, 2, 5, Confidence.MEDIUM),
        # TV版/无修版 and 简／繁 brackets are noise: no fansub, parse still clean.
        (
            "尼古喵喵 EP03.mkv",
            "[TV版&无修版] 尼古喵喵 - EP03 [简／繁]",
            Segment.EPISODE,
            None,
            3,
            Confidence.MEDIUM,
        ),
    ],
)
def test_boundary_table(
    name: str,
    folder: str | None,
    expected_segment: Segment,
    expected_season: int | None,
    expected_episode: int | None,
    expected_level: Confidence,
) -> None:
    result = _parse(name, folder)

    assert result is not None
    assert result.segment is expected_segment
    assert result.season == expected_season
    assert result.episode == expected_episode
    assert result.level is expected_level
    assert result.title
    _assert_evidence_contract(result)


@pytest.mark.parametrize(
    ("release_progress", "expected_level"),
    [(2, Confidence.LOW), (3, Confidence.MEDIUM), (None, Confidence.MEDIUM)],
)
def test_release_progress_gate(release_progress: int | None, expected_level: Confidence) -> None:
    raw = RawName(
        name="尼古喵喵.EP03.简繁.1080p.H.264.AAC.SRTx2.mkv",
        folder="[TV版&无修版] 尼古喵喵 - EP03 [简／繁] (1080p H.264 AAC SRTx2)",
        parent_path="Z:/Downloads",
    )

    result = parse_special(raw, ParseContext(release_progress=release_progress))

    assert result is not None
    assert result.episode == 3
    assert result.level is expected_level


def test_pure_digit_name_belongs_to_dialect_g() -> None:
    assert _parse("03.mkv", "Show S02 [Sub]") is None


def test_unstructured_name_returns_none() -> None:
    assert _parse("randomstuff.mkv") is None


def test_version_noise_brackets_never_surface_as_fansub() -> None:
    result = _parse(
        "尼古喵喵.EP03.简繁.1080p.H.264.AAC.SRTx2.mkv",
        "[TV版&无修版] 尼古喵喵 - EP03 [简／繁] (1080p H.264 AAC SRTx2)",
    )

    assert result is not None
    assert result.fansub is None


def test_double_group_name_side_wins_over_folder() -> None:
    result = _parse(
        "Super no Ura de Yani Suu Futari S01 [CR WEB-DL 1080p AVC AAC][SC_TC].mkv",
        "[Nix-Raws] Super no Ura de Yani Suu Futari S01 [CR WEB-DL 1080p AVC AAC][SC_TC]",
    )

    assert result is not None
    assert result.fansub == "SC_TC"
    assert result.evidence["fansub"] == "name"
    # 组名分歧按合并规则整体降一档
    assert result.level is Confidence.MEDIUM


def test_jammed_title_caps_level_at_medium() -> None:
    result = _parse(
        "FateGrand Order Shuukyoku Tokuiten - Kani Jikan Shinden Solomon 2021 [7³ACG].mkv",
        "[BDrip] FateGrand Order Shuukyoku Tokuiten - Kani Jikan Shinden Solomon 2021 [7³ACG]",
    )

    assert result is not None
    assert result.segment is Segment.MOVIE
    assert result.level is Confidence.MEDIUM
    assert result.missing_fields == ()


def test_folder_only_season_fills_missing_structure() -> None:
    result = _parse("[Sub] Show E07.mkv", "Show S02 [Sub]")

    assert result is not None
    assert result.title == "Show"
    assert result.season == 2
    assert result.episode == 7
    assert result.evidence["season"] == "folder"
