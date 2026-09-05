"""Unit tests for the T5 orchestrator segment: L1 -> L2 routing, degradation
and the learn-confirm-replay roundtrip over the whole L1 fixture corpus."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from autoanime.core.enums import Confidence, MemoryStatus, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.core.models import BypassList, ParseMemory
from autoanime.memory.learn import StorageMemoryAccess, learn_confirmation
from autoanime.memory.lookup import StorageMemoryStore
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l2 import KEY_LEVEL_SERIES, key_hash, level1_key, pattern_hash
from autoanime.pipeline.orchestrator import (
    ROUTE_ARCHIVE,
    ROUTE_L3,
    ROUTE_MEMORY,
    Orchestrator,
)
from tests.support.fixtures import FixtureCase, FixtureExpected, load_all

RAW_NAME = "Anime.AzurLane.Slow.Ahead.E03.1080p.Baha.WEB-DL.mkv"


# --- fakes -------------------------------------------------------------------


class FakeRecognizer:
    """L1 stand-in returning one preset result."""

    def __init__(self, result: ParseResult | None) -> None:
        self.result = result
        self.calls = 0

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        self.calls += 1
        return self.result


@dataclass
class FakeMemoryRow:
    key_level: int
    key_hash: str
    result: dict[str, object] = field(default_factory=dict)
    hit_count: int = 0
    corrected_count: int = 0
    status: str = "active"
    title_shape: str | None = None


class FakeMemoryStore:
    """In-memory ``MemoryStore`` fake: rows keyed by (key_level, key_hash)."""

    def __init__(self, *rows: FakeMemoryRow, bypassed: frozenset[str] = frozenset()) -> None:
        self._rows = {(row.key_level, row.key_hash): row for row in rows}
        self.bypassed = bypassed
        self.recorded_hits: list[Any] = []
        self.recorded_corrections: list[Any] = []
        self.lookup_calls = 0

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None:
        self.lookup_calls += 1
        return self._rows.get((key_level, key_hash))

    async def record_hit(self, parse_memory: Any) -> None:
        self.recorded_hits.append(parse_memory)

    async def record_correction(self, parse_memory: Any) -> None:
        self.recorded_corrections.append(parse_memory)

    async def has_bypass(self, pattern_hash: str) -> bool:
        return pattern_hash in self.bypassed


class BrokenMemoryStore:
    """A store whose every call raises: the unavailable-storage case."""

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None:
        raise RuntimeError("store unavailable")

    async def record_hit(self, parse_memory: Any) -> None:
        raise RuntimeError("store unavailable")

    async def record_correction(self, parse_memory: Any) -> None:
        raise RuntimeError("store unavailable")

    async def has_bypass(self, pattern_hash: str) -> bool:
        raise RuntimeError("store unavailable")


# --- helpers -----------------------------------------------------------------


def _raw() -> RawName:
    return RawName(name=RAW_NAME)


def _series_row(title: str = "Anime AzurLane Slow Ahead", **overrides: Any) -> FakeMemoryRow:
    """A series-level ACTIVE row stored under the title's level-1 key."""
    defaults: dict[str, Any] = {
        "key_level": KEY_LEVEL_SERIES,
        "key_hash": key_hash(level1_key(title)),
        "result": {
            "title": title,
            "season": 2,
            "episode": None,
            "segment": "season_pack",
            "fansub": "MWeb",
        },
    }
    defaults.update(overrides)
    return FakeMemoryRow(**defaults)


def _medium() -> ParseResult:
    """L1 MEDIUM shape: season and fansub missing."""
    return ParseResult(
        title="Anime AzurLane Slow Ahead",
        season=None,
        episode=3,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=("season",),
        evidence={"title": "name", "season": "none", "episode": "name", "segment": "name", "fansub": "none"},
    )


def _high() -> ParseResult:
    return ParseResult(
        title="Anime AzurLane Slow Ahead",
        season=2,
        episode=3,
        segment=Segment.EPISODE,
        fansub="MWeb",
        level=Confidence.HIGH,
        confidence=1.0,
        missing_fields=(),
        evidence={"title": "name", "season": "name", "episode": "name", "segment": "name", "fansub": "name"},
    )


def _low() -> ParseResult:
    return ParseResult(
        title="???",
        season=None,
        episode=None,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.LOW,
        confidence=0.3,
        missing_fields=("season", "episode"),
        evidence={"title": "name"},
    )


def _assert_l1_matches_expected(
    result: ParseResult, expected: FixtureExpected, *, first_file: bool
) -> None:
    assert result.title == expected.title
    assert result.season == expected.season
    if first_file:
        # 第一个文件锁定 expected.episode，其余文件各自解析出集数（语料约定）。
        assert result.episode == expected.episode
    else:
        assert result.episode is not None
    assert result.segment.value == expected.segment
    assert result.fansub == expected.fansub
    assert result.level.value == expected.level


_PROTECTED = frozenset({"name", "folder"})
_FIELDS = ("title", "season", "episode", "segment", "fansub")


def _assert_memory_enhancement_contract(
    enhanced: ParseResult, l1: ParseResult, confirmed: ParseResult, expected_level: Confidence
) -> None:
    """The PR4 merge contract as the orchestrator surfaces it downstream."""
    assert enhanced.evidence.get("key_level") in {"memory:1", "memory:2"}
    filled: list[str] = []
    for field_name in _FIELDS:
        l1_value = getattr(l1, field_name)
        new_value = getattr(enhanced, field_name)
        confirmed_value = getattr(confirmed, field_name)
        if l1_value is not None:
            # L1 already decided this field: memory must never overwrite it.
            assert new_value == l1_value, field_name
            assert enhanced.evidence.get(field_name) == l1.evidence.get(field_name)
            if l1.evidence.get(field_name) in _PROTECTED:
                continue
        if confirmed_value is not None and l1_value is None:
            # Absent in L1, present in memory: filled with memory evidence.
            assert new_value == confirmed_value, field_name
            assert enhanced.evidence.get(field_name) == "memory"
            filled.append(field_name)
        else:
            assert new_value == l1_value, field_name
    assert enhanced.level is expected_level
    assert enhanced.confidence == (1.0 if expected_level is Confidence.HIGH else 0.6)


# --- fixed routing -----------------------------------------------------------


async def test_l1_high_archives_without_entering_l2() -> None:
    store = FakeMemoryStore(_series_row())
    recognizer = FakeRecognizer(_high())
    outcome = await Orchestrator(recognizer, memory_store=store).process(_raw())

    assert outcome.route == ROUTE_ARCHIVE
    assert outcome.l2_applied is False
    assert outcome.degraded is False
    assert outcome.result is recognizer.result
    assert recognizer.calls == 1
    assert store.lookup_calls == 0  # HIGH never consults memory
    assert store.recorded_hits == []


async def test_l1_medium_memory_hit_fuses_and_routes_memory() -> None:
    store = FakeMemoryStore(_series_row())
    outcome = await Orchestrator(FakeRecognizer(_medium()), memory_store=store).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.l2_applied is True
    assert outcome.degraded is False
    assert outcome.result is not None
    assert outcome.result.season == 2
    assert outcome.result.fansub == "MWeb"
    assert outcome.result.level is Confidence.HIGH
    assert outcome.result.evidence["season"] == "memory"
    assert outcome.result.evidence["key_level"] == "memory:1"
    assert len(store.recorded_hits) == 1


async def test_l1_medium_memory_miss_keeps_l1_result_and_routes_l3() -> None:
    store = FakeMemoryStore()  # empty memory: always a miss
    l1 = _medium()
    outcome = await Orchestrator(FakeRecognizer(l1), memory_store=store).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.l2_applied is False
    assert outcome.degraded is False
    assert outcome.result == l1
    assert store.recorded_hits == []


async def test_l1_medium_bypassed_raw_name_neither_fuses_nor_records() -> None:
    store = FakeMemoryStore(_series_row(), bypassed=frozenset({pattern_hash(RAW_NAME)}))
    outcome = await Orchestrator(FakeRecognizer(_medium()), memory_store=store).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.l2_applied is False
    assert outcome.result == _medium()
    # The authoritative raw-name gate fires before any memory lookup.
    assert store.lookup_calls == 0
    assert store.recorded_hits == []


async def test_l1_low_routes_l3_without_touching_memory() -> None:
    store = FakeMemoryStore(_series_row())
    outcome = await Orchestrator(FakeRecognizer(_low()), memory_store=store).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.l2_applied is False
    assert outcome.result == _low()
    assert store.lookup_calls == 0
    assert store.recorded_hits == []


async def test_l1_none_routes_l3_placeholder() -> None:
    store = FakeMemoryStore(_series_row())
    outcome = await Orchestrator(FakeRecognizer(None), memory_store=store).process(_raw())

    assert outcome.result is None
    assert outcome.route == ROUTE_L3
    assert outcome.degraded is False
    assert store.lookup_calls == 0


async def test_parse_shortcut_returns_only_the_result() -> None:
    orchestrator = Orchestrator(FakeRecognizer(_high()), memory_store=FakeMemoryStore())
    assert await orchestrator.parse(_raw()) == _high()
    assert await Orchestrator(FakeRecognizer(None)).parse(_raw()) is None


# --- graceful degradation ----------------------------------------------------


async def test_l2_disabled_by_config_routes_l1_only() -> None:
    store = FakeMemoryStore(_series_row())
    l1 = _medium()
    outcome = await Orchestrator(FakeRecognizer(l1), memory_store=store, l2_enabled=False).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.result == l1
    assert outcome.degraded is False  # config-off is expected, not a failure
    assert store.lookup_calls == 0
    assert store.recorded_hits == []


async def test_missing_store_degrades_for_medium_results() -> None:
    l1 = _medium()
    outcome = await Orchestrator(FakeRecognizer(l1)).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.result == l1
    assert outcome.degraded is True


async def test_broken_store_degrades_without_crashing() -> None:
    l1 = _medium()
    outcome = await Orchestrator(FakeRecognizer(l1), memory_store=BrokenMemoryStore()).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.result == l1
    assert outcome.degraded is True


async def test_high_result_ignores_broken_store() -> None:
    outcome = await Orchestrator(FakeRecognizer(_high()), memory_store=BrokenMemoryStore()).process(_raw())

    assert outcome.route == ROUTE_ARCHIVE
    assert outcome.degraded is False


# --- trust / fusion boundaries ----------------------------------------------


async def test_trust_below_fusion_threshold_supplements_without_fusion() -> None:
    # trust = 1/(1+1) = 0.5: the row participates but may not raise the level.
    store = FakeMemoryStore(_series_row(hit_count=1, corrected_count=1))
    outcome = await Orchestrator(FakeRecognizer(_medium()), memory_store=store).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.result is not None
    assert outcome.result.season == 2
    assert outcome.result.evidence["season"] == "memory"
    assert outcome.result.level is Confidence.MEDIUM
    assert outcome.result.confidence == 0.6
    assert len(store.recorded_hits) == 1


async def test_trust_below_pending_threshold_counts_as_miss() -> None:
    # trust = 1/(1+3) = 0.25 < 0.5: the row is invisible to the query side.
    store = FakeMemoryStore(_series_row(hit_count=1, corrected_count=3))
    l1 = _medium()
    outcome = await Orchestrator(FakeRecognizer(l1), memory_store=store).process(_raw())

    assert outcome.route == ROUTE_L3
    assert outcome.result == l1
    assert store.recorded_hits == []


async def test_memory_never_overwrites_name_evidence_fields() -> None:
    l1 = ParseResult(
        title="Anime AzurLane Slow Ahead",
        season=1,
        episode=3,
        segment=Segment.EPISODE,
        fansub=None,
        level=Confidence.MEDIUM,
        confidence=0.6,
        missing_fields=(),
        evidence={"title": "name", "season": "name", "episode": "name", "segment": "name", "fansub": "none"},
    )
    store = FakeMemoryStore(_series_row())  # memory claims season=2, fansub=MWeb
    outcome = await Orchestrator(FakeRecognizer(l1), memory_store=store).process(_raw())

    assert outcome.route == ROUTE_MEMORY
    assert outcome.result is not None
    assert outcome.result.season == 1  # L1 name evidence wins
    assert outcome.result.evidence["season"] == "name"
    assert outcome.result.fansub == "MWeb"  # absent field filled from memory
    assert outcome.result.evidence["fansub"] == "memory"
    assert outcome.result.level is Confidence.HIGH  # a trusted hit filled a field


# --- write-side bypass gate (contract decision 5) -----------------------------


async def test_bypassed_confirmation_writes_no_memory_rows(tmp_path: Path) -> None:
    async with SqliteStorage(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}") as storage:
        access = StorageMemoryAccess(storage)
        await storage.add(
            BypassList(pattern_hash=pattern_hash(RAW_NAME), reason="junk release")
        )

        outcome = await learn_confirmation(
            access,
            confirmed=ParseResult(
                title="Anime AzurLane Slow Ahead",
                season=2,
                episode=3,
                segment=Segment.EPISODE,
                fansub="MWeb",
                level=Confidence.HIGH,
                confidence=1.0,
                missing_fields=(),
                evidence={},
            ),
            raw_name=RAW_NAME,
            bypass_lookup=access,
        )

        assert outcome.bypassed is True
        assert outcome.entries == ()
        assert await storage.list(ParseMemory) == []


# --- 26-fixture learn -> confirm -> replay roundtrip -------------------------

_ROUNDTRIP_CASES = load_all()


def test_fixture_corpus_has_26_cases() -> None:
    assert len(_ROUNDTRIP_CASES) == 26


def _series_rows(rows: list[Any]) -> list[Any]:
    return [row for row in rows if row.key_level == KEY_LEVEL_SERIES]


@pytest.mark.parametrize("case", _ROUNDTRIP_CASES, ids=lambda case: case.id)
async def test_learn_confirm_replay_roundtrip(case: FixtureCase, tmp_path: Path) -> None:
    expected = case.expected
    assert expected is not None

    async with SqliteStorage(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}") as storage:
        access = StorageMemoryAccess(storage)
        orchestrator = Orchestrator(memory_store=StorageMemoryStore(storage))

        # Phase 1: L1 first pass on every file while memory is still empty.
        parsed: list[tuple[RawName, ParseResult, ParseResult]] = []
        for index, raw in enumerate(case.to_raw_names()):
            l1 = await LocalRecognizer().parse(raw)
            assert l1 is not None
            _assert_l1_matches_expected(l1, expected, first_file=index == 0)
            first = await orchestrator.process(raw)
            assert first.result == l1
            if expected.level == "high":
                assert first.route == ROUTE_ARCHIVE
            else:
                assert expected.level == "medium"
                assert first.route == ROUTE_L3
                assert first.degraded is False
            # 用户确认：字段取 L1 期望（集数按本文件实际解析值），可信级别。
            confirmed = ParseResult(
                title=expected.title,
                season=expected.season,
                episode=l1.episode,
                segment=Segment(expected.segment),
                fansub=expected.fansub,
                level=Confidence.HIGH,
                confidence=1.0,
                missing_fields=(),
                evidence={},
            )
            parsed.append((raw, l1, confirmed))

        # Phase 2: learn every confirmed result.
        for raw, _l1, confirmed in parsed:
            learned = await learn_confirmation(
                access, confirmed=confirmed, raw_name=raw.name, bypass_lookup=access
            )
            assert learned.bypassed is False
            assert len(learned.entries) == 2
            assert all(entry.status is MemoryStatus.ACTIVE for entry in learned.entries)

        # Phase 3: replay -- the flywheel returns the fused result.
        for _raw, l1, confirmed in parsed:
            replay = await orchestrator.process(_raw)
            assert replay.result is not None
            if expected.level == "high":
                assert replay.route == ROUTE_ARCHIVE
                assert replay.result == l1
                assert "key_level" not in replay.result.evidence
            else:
                assert replay.route == ROUTE_MEMORY
                assert replay.l2_applied is True
                _assert_memory_enhancement_contract(replay.result, l1, confirmed, Confidence.MEDIUM)

        # Every consumed hit was counted on the series-level rows it used
        # (HIGH cases never enter L2, so their rows stay at zero hits).
        for row in _series_rows(await storage.list(ParseMemory)):
            if expected.level == "medium":
                assert row.hit_count >= 1
            else:
                assert row.hit_count == 0
