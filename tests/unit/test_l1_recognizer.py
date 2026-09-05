"""LocalRecognizer (L1 aggregator) unit tests.

Covers: fixture parity across all 26 dialect fixtures, Recognizer protocol
conformance, folder-conflict downgrade, the release_progress gate,
unparseable -> None, deterministic tie-breaking, and the aggregate-level
quality gates.
"""

from __future__ import annotations

import asyncio

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName, Recognizer
from autoanime.pipeline.l1_local import DIALECT_PIPELINE, LocalRecognizer
from tests.support.fixtures import FixtureCase, load_all

_RECOGNIZER = LocalRecognizer()


def _parse(
    name: str,
    folder: str | None = None,
    context: ParseContext | None = None,
    parent_path: str = "Z:/Downloads",
) -> ParseResult | None:
    return asyncio.run(
        _RECOGNIZER.parse(RawName(name=name, folder=folder, parent_path=parent_path), context)
    )


# ---------------------------------------------------------------------------
# Fixture parity: every fixture must come out exactly as its expected.json.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", load_all(), ids=lambda case: case.id)
def test_fixture_matches_expected_through_aggregator(case: FixtureCase) -> None:
    expected = case.expected
    assert expected is not None, f"missing expected.json for {case.id}"
    for index, raw in enumerate(case.to_raw_names()):
        result = asyncio.run(_RECOGNIZER.parse(raw))
        assert result is not None, f"{case.id} file {index}: aggregator returned None"
        assert result.title == expected.title
        assert result.season == expected.season
        if index == 0:
            # expected.json 锁定第一个文件的集数；其余文件各自解析出集数
            assert result.episode == expected.episode
        else:
            assert result.episode is not None
        assert result.segment.value == expected.segment
        assert result.fansub == expected.fansub
        assert result.level.value == expected.level
        assert result.confidence == expected.confidence
        assert result.missing_fields == expected.missing_fields
        assert result.evidence == expected.evidence


def test_pipeline_order_is_fixed() -> None:
    names = {dialect.__module__.rsplit(".", 1)[-1] for dialect in DIALECT_PIPELINE}
    assert names == {
        "dot",
        "bracket",
        "pure_bracket",
        "cjk",
        "ep",
        "special",
        "minimal",
    }
    assert len(DIALECT_PIPELINE) == 7


def test_recognizer_satisfies_protocol() -> None:
    assert isinstance(_RECOGNIZER, Recognizer)


# ---------------------------------------------------------------------------
# Context handling.
# ---------------------------------------------------------------------------


def test_folder_conflict_prefers_filename_and_downgrades() -> None:
    result = _parse(
        "Some.Title.S01E01.1080p.Baha.WEB-DL-MWeb.mkv",
        folder="Some.Title.S03.1080p.Baha.WEB-DL-MWeb",
    )
    assert result is not None
    assert result.season == 1
    assert result.episode == 1
    assert result.evidence["season"] == "name"
    assert result.level is Confidence.MEDIUM
    assert result.confidence == 0.6


def test_folder_fills_missing_fields() -> None:
    result = _parse(
        "Some.Title.1080p.Baha.WEB-DL-MWeb.mkv",
        folder="Some.Title.S02.1080p.Baha.WEB-DL-MWeb",
    )
    assert result is not None
    assert result.season == 2
    assert result.evidence["season"] == "folder"
    assert result.missing_fields == ()


def test_episode_beyond_release_progress_drops_to_low() -> None:
    result = _parse(
        "Please.Excuse.My.Younger.Brothers.S01E02.1080p.friDay.WEB-DL.AAC2.0.H.264-MWeb.mkv",
        context=ParseContext(release_progress=1),
    )
    assert result is not None
    assert result.episode == 2
    assert result.level is Confidence.LOW
    assert result.confidence == 0.2
    assert result.evidence["release_progress"] == "context"


def test_episode_within_release_progress_keeps_level() -> None:
    result = _parse(
        "Please.Excuse.My.Younger.Brothers.S01E02.1080p.friDay.WEB-DL.AAC2.0.H.264-MWeb.mkv",
        context=ParseContext(release_progress=2),
    )
    assert result is not None
    assert result.episode == 2
    assert result.level is Confidence.HIGH
    # context 提供 release_progress 时证据总是记录来源（即使未触发降档）
    assert result.evidence["release_progress"] == "context"


# ---------------------------------------------------------------------------
# Unparseable inputs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Just Some Random Text.mkv",
        "random_text_only",
        # Technical/noise-only names must not become title memories.
        "1080p.WEB-DL.mkv",
        "WEBRip.AAC.mkv",
        "H.264.mkv",
        "招募翻译.mp4",
        "1080p",
        "webrip",
    ],
)
def test_unrecognizable_names_return_none(name: str) -> None:
    assert _parse(name) is None


# ---------------------------------------------------------------------------
# Deterministic tie-breaking (injected dialects, no global state).
# ---------------------------------------------------------------------------


def _result(title: str, level: Confidence) -> ParseResult:
    from autoanime.pipeline.l1.confidence import confidence_for

    return ParseResult(
        title=title,
        season=None,
        episode=1,
        segment=Segment.EPISODE,
        fansub=None,
        level=level,
        confidence=confidence_for(level),
        evidence={"title": "name", "episode": "name", "segment": "name"},
    )


def test_equal_confidence_keeps_earlier_dialect_in_fixed_order() -> None:
    calls: list[int] = []

    def first(raw: RawName, context: ParseContext | None) -> ParseResult:
        calls.append(1)
        return _result("First Title", Confidence.MEDIUM)

    def second(raw: RawName, context: ParseContext | None) -> ParseResult:
        calls.append(2)
        return _result("Second Title", Confidence.MEDIUM)

    recognizer = LocalRecognizer(dialects=[first, second])
    result = asyncio.run(recognizer.parse(RawName(name="Anything")))

    assert calls == [1, 2], "every dialect runs exactly once, in fixed order"
    assert result is not None
    assert result.title == "First Title"


def test_higher_confidence_beats_fixed_order() -> None:
    def first(raw: RawName, context: ParseContext | None) -> ParseResult:
        return _result("Low Title", Confidence.LOW)

    def second(raw: RawName, context: ParseContext | None) -> ParseResult:
        return _result("High Title", Confidence.HIGH)

    recognizer = LocalRecognizer(dialects=[first, second])
    result = asyncio.run(recognizer.parse(RawName(name="Anything")))

    assert result is not None
    assert result.title == "High Title"


def test_same_confidence_prefers_more_complete_result() -> None:
    def first(raw: RawName, context: ParseContext | None) -> ParseResult:
        return _result("Sparse Title", Confidence.MEDIUM)

    def second(raw: RawName, context: ParseContext | None) -> ParseResult:
        result = _result("Complete Title", Confidence.MEDIUM)
        return ParseResult(
            title=result.title,
            season=2,
            episode=result.episode,
            segment=result.segment,
            fansub=result.fansub,
            level=result.level,
            confidence=result.confidence,
            missing_fields=(),
            evidence={"title": "name", "season": "name", "episode": "name", "segment": "name"},
        )

    recognizer = LocalRecognizer(dialects=[first, second])
    result = asyncio.run(recognizer.parse(RawName(name="Anything")))

    assert result is not None
    assert result.title == "Complete Title"


# ---------------------------------------------------------------------------
# Aggregate quality gates.
# ---------------------------------------------------------------------------


def test_title_with_leaked_brackets_is_discarded() -> None:
    # dot 方言对纯方括号名会产生带 '[' 的标题候选，聚合器必须丢弃它，
    # 让真正的方括号方言胜出（B01 的回归）。
    result = _parse(
        "[BeanSub&FZSD&LoliHouse] BLEACH Sennen Kessen-hen - 41 [WebRip 1080p HEVC-10bit AAC ASSx2]"
    )
    assert result is not None
    assert result.title == "BLEACH Sennen Kessen-hen"
    assert result.episode == 41
    assert result.fansub == "BeanSub&FZSD&LoliHouse"
    assert "[" not in result.title


def test_all_caps_title_caps_high_result_at_medium() -> None:
    # special 方言对全大写标题给出 HIGH，聚合器按合同钳制到 MEDIUM（A04 的回归）。
    result = _parse(
        "BLACK.TORCH.S01.1080p.Baha.WEB-DL.AAC2.0.H.264-MWeb.mkv"
    )
    assert result is not None
    assert result.title == "BLACK TORCH"
    assert result.level is Confidence.MEDIUM


def test_dialect_value_error_counts_as_no_hit() -> None:
    # dot 方言对纯方括号批量集名会抛 ValueError（B03 的回归）：
    # 聚合器必须把它当作未命中，而不是让 pipeline 崩溃。
    result = _parse(
        "[Nekomoe kissaten&LoliHouse] Akane-banashi [03-06][WebRip 1080p HEVC-10bit AAC ASSx2].mkv"
    )
    assert result is not None
    assert result.title == "Akane-banashi"
    assert result.episode == 3


def test_pipeline_entries_are_callable() -> None:
    for dialect in DIALECT_PIPELINE:
        assert callable(dialect)
