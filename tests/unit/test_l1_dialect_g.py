"""Dialect G (minimal) unit tests: fixture consistency and boundary values."""

from __future__ import annotations

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.dialects.minimal import parse_minimal
from tests.support.fixtures import FixtureCase, load_dialects

_EVIDENCE_FIELDS = frozenset({"title", "season", "episode", "segment", "fansub"})
_EVIDENCE_SOURCES = frozenset({"name", "folder", "context", "none"})

G01_FOLDER = "Anime.AzurLane.Slow.Ahead.S02.1080p.Baha.WEB-DL.AAC2.0.H.264-MWeb"
G02_FOLDER = "[BeanSub&FZSD&LoliHouse] BLEACH Sennen Kessen-hen [WebRip 1080p HEVC-10bit AAC ASSx2]"
G03_FOLDER = "[SweetSub] Honzuki no Gekokujou S04"


def _parse(
    name: str, folder: str | None = None, context: ParseContext | None = None
) -> ParseResult | None:
    return parse_minimal(RawName(name=name, folder=folder, parent_path="Z:/Downloads"), context)


def _assert_evidence_contract(result: ParseResult) -> None:
    assert set(result.evidence) == _EVIDENCE_FIELDS
    assert set(result.evidence.values()) <= _EVIDENCE_SOURCES
    assert result.confidence == {Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.6, Confidence.LOW: 0.2}[
        result.level
    ]


@pytest.mark.parametrize("case", load_dialects("G"), ids=lambda case: case.id)
def test_fixture_expected_matches_parse(case: FixtureCase) -> None:
    assert case.expected is not None
    expected = case.expected
    for index, raw in enumerate(case.to_raw_names()):
        result = _parse(raw.name, raw.folder)
        assert result is not None
        if index == 0:
            assert result.episode == expected.episode
        else:
            # 第一个文件锁定 expected.episode，其余文件各自解析出集数
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
    ("name", "folder", "expected_season", "expected_episode", "expected_level"),
    [
        # 前导零与补零宽度都不影响集数
        ("01.mkv", G01_FOLDER, 2, 1, Confidence.MEDIUM),
        ("07.mkv", G03_FOLDER, 4, 7, Confidence.HIGH),
        # 无补零文件名同样成立
        ("7.mp4", G03_FOLDER, 4, 7, Confidence.HIGH),
        # 高集号 + 篇名文件夹（季号不可知）
        ("41.mkv", G02_FOLDER, None, 41, Confidence.MEDIUM),
    ],
)
def test_boundary_table(
    name: str,
    folder: str,
    expected_season: int | None,
    expected_episode: int,
    expected_level: Confidence,
) -> None:
    result = _parse(name, folder)

    assert result is not None
    assert result.segment is Segment.EPISODE
    assert result.season == expected_season
    assert result.episode == expected_episode
    assert result.level is expected_level
    assert result.title
    _assert_evidence_contract(result)


@pytest.mark.parametrize(
    ("name", "folder"),
    [
        # 极简文件名缺 folder：无法给出有意义结果
        ("01.mkv", None),
        # folder 无标题信息（纯技术标记）
        ("01.mkv", "1080p WEB-DL"),
        # 非「纯数字 + 扩展名」不属于方言 G
        ("[BeanSub] BLEACH Sennen Kessen-hen - 41 [WebRip 1080p].mkv", G02_FOLDER),
        ("S01E05.mkv", G01_FOLDER),
    ],
)
def test_unresolvable_inputs_return_none(name: str, folder: str | None) -> None:
    assert _parse(name, folder) is None


def test_title_season_fansub_only_from_folder() -> None:
    result = _parse("01.mkv", G01_FOLDER)

    assert result is not None
    assert result.evidence["title"] == "folder"
    assert result.evidence["season"] == "folder"
    assert result.evidence["fansub"] == "folder"
    assert result.evidence["episode"] == "name"
    assert result.evidence["segment"] == "name"


def test_release_progress_gate() -> None:
    beyond = _parse("41.mkv", G02_FOLDER, ParseContext(release_progress=40))
    at_progress = _parse("41.mkv", G02_FOLDER, ParseContext(release_progress=41))

    assert beyond is not None and beyond.level is Confidence.LOW
    assert at_progress is not None and at_progress.level is Confidence.MEDIUM


def test_folder_without_season_caps_level_at_medium() -> None:
    result = _parse("41.mkv", G02_FOLDER)

    assert result is not None
    assert result.season is None
    assert result.missing_fields == ("season",)
    assert result.level is Confidence.MEDIUM
