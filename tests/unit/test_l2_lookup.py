"""Unit tests for the L2 memory query path: two-level lookup and fusion (PR4 T3)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from autoanime.core.enums import Confidence, MemoryStatus, Segment
from autoanime.core.interfaces import (
    MemoryRecognizer,
    MemoryStore,
    ParseResult,
    RawName,
)
from autoanime.core.models import AuditLog, BypassList, ParseMemory
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.lookup import (
    StorageMemoryStore,
    enhance_result,
    exact_key,
    hit_from_memory,
    lookup_memory,
    series_key,
)
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l2.bypass import pattern_hash
from autoanime.pipeline.l2.keys import (
    KEY_LEVEL_EXACT,
    KEY_LEVEL_SERIES,
    key_hash,
    level1_key,
    level2_key,
)
from autoanime.pipeline.l2_memory import MemoryEnhancer

LOOKUP_ROOT = Path(__file__).parents[1] / "fixtures" / "memory" / "roundtrip" / "lookup"


# --- helpers ----------------------------------------------------------------


def _l1_azurlane() -> ParseResult:
    """L1 output shape of the AzurLane dot sample: MEDIUM, season missing."""
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
        self.recorded_hits: list[FakeMemoryRow] = []
        self.recorded_corrections: list[FakeMemoryRow] = []

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None:
        return self._rows.get((key_level, key_hash))

    async def record_hit(
        self, parse_memory: Any, *, operation_id: str | None = None
    ) -> None:
        self.recorded_hits.append(parse_memory)

    async def record_correction(self, parse_memory: Any) -> None:
        self.recorded_corrections.append(parse_memory)

    async def has_bypass(self, pattern_hash: str) -> bool:
        return pattern_hash in self.bypassed


def _row_for(title: str, **overrides: Any) -> FakeMemoryRow:
    """A series-level ACTIVE row stored under the title's level-1 key."""
    defaults: dict[str, Any] = {
        "key_level": KEY_LEVEL_SERIES,
        "key_hash": key_hash(level1_key(title)),
        "result": {"title": title, "season": 2, "episode": None, "segment": "season_pack", "fansub": "MWeb"},
    }
    defaults.update(overrides)
    return FakeMemoryRow(**defaults)


def _load_lookup_cases() -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(LOOKUP_ROOT.glob("*.json"))]


def _memory_row_from_fixture(memory: dict[str, Any]) -> FakeMemoryRow:
    """Mirror the learning side: store the fixture row under its own key."""
    result = memory["result"]
    if memory["key_level"] == KEY_LEVEL_SERIES:
        key = level1_key(result["title"])
    else:
        key = level2_key(result["title"], result["season"], result["episode"], result["fansub"])
    return FakeMemoryRow(
        key_level=memory["key_level"],
        key_hash=key_hash(key),
        result=dict(result),
        hit_count=memory["hit_count"],
        corrected_count=memory["corrected_count"],
        status=memory["status"],
    )


def _assert_matches(enhanced: ParseResult, expected: dict[str, Any]) -> None:
    assert enhanced.title == expected["title"]
    assert enhanced.season == expected["season"]
    assert enhanced.episode == expected["episode"]
    assert enhanced.segment.value == expected["segment"]
    assert enhanced.fansub == expected["fansub"]
    assert enhanced.level.value == expected["level"]
    assert enhanced.confidence == expected["confidence"]
    assert list(enhanced.missing_fields) == list(expected["missing_fields"])
    assert enhanced.evidence == expected["evidence"]


# --- key derivation ---------------------------------------------------------


def test_series_and_exact_keys_derive_from_l1_result() -> None:
    result = _l1_azurlane()
    assert series_key(result) == "anime azurlane slow ahead"
    assert exact_key(result) == "anime azurlane slow ahead|s=-|e=3|f=-"


# --- hit drafting and gating ------------------------------------------------


def test_hit_from_memory_recovers_active_row() -> None:
    row = _row_for("Anime AzurLane Slow Ahead", hit_count=3)
    hit = hit_from_memory(row, key_level=KEY_LEVEL_SERIES)

    assert hit is not None
    assert hit.key_level == KEY_LEVEL_SERIES
    assert hit.trust == 1.0
    assert hit.season == 2
    assert hit.fansub == "MWeb"


@pytest.mark.parametrize("status", ["pending", "deprecated", MemoryStatus.PENDING])
def test_hit_from_memory_filters_non_active_status(status: Any) -> None:
    row = _row_for("Anime AzurLane Slow Ahead", status=status)
    assert hit_from_memory(row, key_level=KEY_LEVEL_SERIES) is None


def test_hit_from_memory_treats_trust_below_pending_threshold_as_miss() -> None:
    row = _row_for("Anime AzurLane Slow Ahead", hit_count=1, corrected_count=3)
    assert hit_from_memory(row, key_level=KEY_LEVEL_SERIES) is None


def test_hit_from_memory_backfills_title_from_title_shape() -> None:
    row = _row_for(
        "unused",
        result={"season": 2, "episode": 3},
        title_shape="some anime title s{season}e{ep}",
    )
    hit = hit_from_memory(row, key_level=KEY_LEVEL_SERIES)

    assert hit is not None
    assert hit.title == "some anime title s2e3"
    assert hit.season == 2
    assert hit.episode == 3


# --- two-level lookup -------------------------------------------------------


async def test_lookup_prefers_series_level() -> None:
    result = _l1_azurlane()
    series_row = _row_for(result.title)
    exact_row = FakeMemoryRow(
        key_level=KEY_LEVEL_EXACT,
        key_hash=key_hash(exact_key(result)),
        result={"title": result.title, "season": 1, "episode": 3, "segment": "episode", "fansub": "MWeb"},
    )
    store = FakeMemoryStore(series_row, exact_row)

    match = await lookup_memory(result, store)

    assert match is not None
    assert match.hit.key_level == KEY_LEVEL_SERIES
    assert match.memory is series_row


async def test_lookup_falls_back_to_exact_level() -> None:
    result = _l1_azurlane()
    exact_row = FakeMemoryRow(
        key_level=KEY_LEVEL_EXACT,
        key_hash=key_hash(exact_key(result)),
        result={"title": result.title, "season": 1, "episode": 3, "segment": "episode", "fansub": "MWeb"},
    )
    store = FakeMemoryStore(exact_row)

    match = await lookup_memory(result, store)

    assert match is not None
    assert match.hit.key_level == KEY_LEVEL_EXACT
    assert match.hit.season == 1


async def test_lookup_misses_when_only_inactive_rows() -> None:
    row = _row_for(_l1_azurlane().title, status="pending")
    assert await lookup_memory(_l1_azurlane(), FakeMemoryStore(row)) is None


async def test_lookup_misses_on_empty_store() -> None:
    match = await lookup_memory(_l1_azurlane(), FakeMemoryStore())
    assert match is None


# --- enhance: bypass, fusion and hit recording ------------------------------


async def test_enhance_fuses_trusted_hit_and_records_it() -> None:
    result = _l1_azurlane()
    store = FakeMemoryStore(_row_for(result.title))

    enhanced = await enhance_result(result, None, store)

    assert enhanced is not None
    assert enhanced.season == 2
    assert enhanced.fansub == "MWeb"
    assert enhanced.level is Confidence.HIGH
    assert enhanced.evidence["season"] == "memory"
    assert enhanced.evidence["key_level"] == "memory:1"
    assert len(store.recorded_hits) == 1
    assert store.recorded_corrections == []


async def test_enhance_returns_none_and_records_nothing_on_empty_store() -> None:
    store = FakeMemoryStore()
    assert await enhance_result(_l1_azurlane(), None, store) is None
    assert store.recorded_hits == []



async def test_enhance_skips_fusion_for_bypassed_raw_name() -> None:
    result = _l1_azurlane()
    raw_name = "Anime.AzurLane.Slow.Ahead.E03.1080p.Baha.WEB-DL.mkv"
    store = FakeMemoryStore(
        _row_for(result.title),
        bypassed=frozenset({pattern_hash(raw_name)}),
    )

    assert await enhance_result(result, None, store, raw_name=raw_name) is None
    assert store.recorded_hits == []



# --- roundtrip lookup fixtures (real L1 + fake store) -----------------------


@pytest.mark.parametrize("case", _load_lookup_cases(), ids=lambda case: case["id"])
async def test_lookup_roundtrip_fixture(case: dict[str, Any]) -> None:
    query = case["query"]
    l1_result = await LocalRecognizer().parse(
        RawName(name=query["name"], folder=query["folder"], parent_path="Z:/Downloads")
    )
    assert l1_result is not None

    memory = case["memory"]
    store = FakeMemoryStore(_memory_row_from_fixture(memory)) if memory else FakeMemoryStore()

    enhanced = await MemoryEnhancer().enhance(l1_result, None, store)
    expected = query["expected"]

    if expected is None:
        assert enhanced is None
        assert store.recorded_hits == []
    else:
        assert enhanced is not None
        _assert_matches(enhanced, expected)
        assert len(store.recorded_hits) == 1


async def test_lookup_fixtures_exist() -> None:
    ids = [case["id"] for case in _load_lookup_cases()]
    assert ids == [
        "L01_no_memory_returns_none",
        "L02_active_memory_fills_and_upgrades",
        "L03_low_trust_supplements_without_fusion",
        "L04_inactive_status_not_fused",
    ]


# --- protocol conformance ---------------------------------------------------


def test_memory_enhancer_satisfies_the_t1_protocol() -> None:
    assert isinstance(MemoryEnhancer(), MemoryRecognizer)
    assert isinstance(FakeMemoryStore(), MemoryStore)


# --- StorageMemoryStore over the real SqliteStorage -------------------------


async def test_storage_memory_store_find_hit_and_bypass(tmp_path: Path) -> None:
    async with SqliteStorage(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}") as storage:
        store = StorageMemoryStore(storage)
        digest = key_hash(level1_key("Anime AzurLane Slow Ahead"))
        await storage.add(
            ParseMemory(
                key_level=KEY_LEVEL_SERIES,
                key_hash=digest,
                result={"title": "Anime AzurLane Slow Ahead", "season": 2, "episode": None},
            )
        )
        await storage.add(
            BypassList(pattern_hash=key_hash("Some.Noisy.Release"), reason="junk release")
        )

        found = await store.find_parse_memory(KEY_LEVEL_SERIES, digest)
        assert found is not None
        assert found.status is MemoryStatus.ACTIVE
        assert await store.find_parse_memory(KEY_LEVEL_EXACT, digest) is None

        await store.record_hit(found)
        await store.record_correction(found)
        refetched = await storage.get(ParseMemory, found.id)
        assert refetched is not None
        assert refetched.hit_count == 1
        assert refetched.corrected_count == 1
        assert refetched.last_hit_at is not None

        assert not await store.has_bypass(digest)
        assert await store.has_bypass(key_hash("Some.Noisy.Release"))


async def test_storage_memory_store_enhances_through_the_real_db(tmp_path: Path) -> None:
    async with SqliteStorage(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}") as storage:
        store = StorageMemoryStore(storage)
        await storage.add(
            ParseMemory(
                key_level=KEY_LEVEL_SERIES,
                key_hash=key_hash(level1_key("Anime AzurLane Slow Ahead")),
                title_shape=level1_key("Anime AzurLane Slow Ahead"),
                result={
                    "title": "Anime AzurLane Slow Ahead",
                    "season": 2,
                    "episode": None,
                    "segment": "season_pack",
                    "fansub": "MWeb",
                },
            )
        )

        l1_result = await LocalRecognizer().parse(
            RawName(name="Anime.AzurLane.Slow.Ahead.E03.1080p.Baha.WEB-DL.mkv", parent_path="Z:/Downloads")
        )
        assert l1_result is not None

        enhanced = await MemoryEnhancer().enhance(l1_result, None, store)

        assert enhanced is not None
        assert enhanced.season == 2
        assert enhanced.fansub == "MWeb"
        assert enhanced.level is Confidence.HIGH

        rows = await storage.list(ParseMemory)
        assert len(rows) == 1
        assert rows[0].hit_count == 1


async def test_storage_memory_store_wires_hit_audit(tmp_path: Path) -> None:
    async with SqliteStorage(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}") as storage:
        store = StorageMemoryStore(storage, audit_governance=MemoryGovernance(storage))
        row = ParseMemory(
            key_level=KEY_LEVEL_SERIES,
            key_hash=key_hash(level1_key("Anime AzurLane Slow Ahead")),
            title_shape=level1_key("Anime AzurLane Slow Ahead"),
            result={
                "title": "Anime AzurLane Slow Ahead",
                "season": 2,
                "episode": None,
                "segment": "season_pack",
                "fansub": "MWeb",
            },
        )
        await storage.add(row)

        l1_result = await LocalRecognizer().parse(
            RawName(name="Anime.AzurLane.Slow.Ahead.E03.1080p.Baha.WEB-DL.mkv", parent_path="Z:/Downloads")
        )
        assert l1_result is not None
        enhanced = await MemoryEnhancer().enhance(l1_result, None, store)

        assert enhanced is not None
        audits = await storage.list(AuditLog)
        assert len(audits) == 1
        assert audits[0].entity == "parse_memory"
        assert audits[0].action == "memory_hit"
        assert audits[0].entity_id == row.id
        assert audits[0].instruction["key_level"] == KEY_LEVEL_SERIES
