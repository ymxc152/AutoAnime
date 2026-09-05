"""Unit tests for L2 governance: bypass, alias, status demotion, audit (PR4 T4)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from autoanime.core.enums import Actor, MemorySource, MemoryStatus
from autoanime.core.models import Alias, AuditLog, ParseMemory, Series
from autoanime.memory.alias import AliasService, alias_norm
from autoanime.memory.governance import (
    ACTION_BYPASS_ADD,
    ACTION_DEMOTE_PENDING,
    ACTION_DEPRECATE,
    ACTION_MEMORY_HIT,
    DEFAULT_NO_HIT_DAYS_FOR_DEPRECATION,
    ENTITY_BYPASS_LIST,
    ENTITY_PARSE_MEMORY,
    MemoryGovernance,
    StatusDecision,
    status_decision,
)
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l2 import pattern_hash

GOVERNANCE_ROOT = Path(__file__).parents[1] / "fixtures" / "memory" / "roundtrip" / "governance"


def _load(name: str) -> dict[str, Any]:
    return json.loads((GOVERNANCE_ROOT / name).read_text(encoding="utf-8"))


@pytest.fixture
async def storage() -> Any:
    async with SqliteStorage("sqlite+aiosqlite:///:memory:") as store:
        yield store


@pytest.fixture
def governance(storage: SqliteStorage) -> MemoryGovernance:
    return MemoryGovernance(storage)


@pytest.fixture
def alias_service(storage: SqliteStorage) -> AliasService:
    return AliasService(storage)


async def _seed_parse_memory(
    storage: SqliteStorage,
    *,
    status: MemoryStatus,
    hit_count: int,
    corrected_count: int,
    last_hit_at: datetime | None,
) -> ParseMemory:
    row = ParseMemory(
        key_level=1,
        key_hash=f"hash-{status.value}-{hit_count}-{corrected_count}",
        title_shape="some title",
        result={},
        source=MemorySource.MANUAL,
        hit_count=hit_count,
        corrected_count=corrected_count,
        last_hit_at=last_hit_at,
        status=status,
    )
    await storage.add(row)
    return row


def _parse_datetime(text: str) -> datetime:
    return datetime.fromisoformat(text)


# --- alias: pure normalization -----------------------------------------------


def test_alias_norm_matches_level1_title_shape_normalization() -> None:
    assert alias_norm("Some.Title_S02E01") == "some title s{season}e{ep}"
    assert alias_norm("Sousou.No.Frieren") == alias_norm("Sousou no Frieren")
    assert alias_norm("葬送的芙莉莲") == "葬送的芙莉莲"


# --- alias: DB lookups -------------------------------------------------------


async def test_alias_roundtrip_from_fixture(alias_service: AliasService) -> None:
    case = _load("alias.json")

    for alias_title in case["aliases"]:
        await alias_service.add_alias(case["series_id"], alias_title)

    for title in case["matching_titles"]:
        assert await alias_service.find_series_id(title) == case["series_id"], title

    for title in case["non_matching_titles"]:
        assert await alias_service.find_series_id(title) is None, title


async def test_add_alias_is_idempotent_and_preserves_source(
    alias_service: AliasService,
) -> None:
    first = await alias_service.add_alias(7, "Sousou no Frieren", source="tmdb")
    second = await alias_service.add_alias(7, "Sousou.No.Frieren")

    assert first.id == second.id
    assert second.source == "tmdb"
    assert len(await alias_service.aliases_for_series(7)) == 1


async def test_find_series_ids_orders_by_registration_and_isolates_series(
    alias_service: AliasService,
) -> None:
    await alias_service.add_alias(2, "Sousou no Frieren")
    await alias_service.add_alias(1, "Sousou.No.Frieren")

    assert await alias_service.find_series_ids("sousou no frieren") == [2, 1]
    assert await alias_service.find_series_id("sousou no frieren") == 2
    assert await alias_service.aliases_for_series(1) != []
    assert await alias_service.aliases_for_series(3) == []


async def test_alias_rows_persist_through_storage(
    storage: SqliteStorage, alias_service: AliasService
) -> None:
    await alias_service.add_alias(1, "葬送的芙莉莲")

    rows = await storage.list(Alias)
    assert len(rows) == 1
    assert rows[0].alias_norm == "葬送的芙莉莲"
    assert rows[0].source == "manual"


# --- bypass: pure side already covered by T1; DB side here --------------------


async def test_bypass_roundtrip_from_fixture(governance: MemoryGovernance) -> None:
    case = _load("bypass.json")

    added = await governance.add_bypass(case["pattern_names"][0], case["reason"])
    assert added.reason == case["reason"]
    assert added.created_at is not None

    for raw_name in case["pattern_names"][1:]:
        digest = pattern_hash(raw_name)
        assert await governance.has_bypass(digest)
        assert await governance.is_bypassed(raw_name)

    assert not await governance.is_bypassed(case["unrelated_name"])
    assert not await governance.has_bypass(pattern_hash(case["unrelated_name"]))


async def test_add_bypass_is_idempotent_on_pattern_hash(
    governance: MemoryGovernance,
) -> None:
    first = await governance.add_bypass("Some.Show.S01E01.mkv", "why")
    second = await governance.add_bypass("some.show.s01e01", "why again")

    assert first.id == second.id
    assert len(await governance.bypassed_hashes()) == 1


async def test_bypassed_hashes_matches_pattern_hash_of_fixture_names(
    governance: MemoryGovernance,
) -> None:
    case = _load("bypass.json")
    await governance.add_bypass(case["pattern_names"][0], case["reason"])

    expected = {pattern_hash(name) for name in case["pattern_names"]}
    assert len(expected) == 1
    assert await governance.bypassed_hashes() == expected


# --- status demotion: pure state machine -------------------------------------


@pytest.mark.parametrize("case", _load("demotion.json")["cases"], ids=lambda case: case["name"])
def test_status_decision_table_from_fixture(case: dict[str, Any]) -> None:
    config = _load("demotion.json")
    now = _parse_datetime(config["now"])
    decision = status_decision(
        current=MemoryStatus(case["status"]),
        hit_count=case["hit_count"],
        corrected_count=case["corrected_count"],
        last_hit_at=_parse_datetime(case["last_hit_at"]) if case["last_hit_at"] else None,
        now=now,
        no_hit_days_for_deprecation=config["no_hit_days_for_deprecation"],
    )

    assert decision == StatusDecision(
        MemoryStatus(case["expected_status"]), case["expected_action"]
    )


def test_status_decision_threshold_constants_come_from_t1() -> None:
    from autoanime.pipeline.l2 import TRUST_PENDING_THRESHOLD

    assert DEFAULT_NO_HIT_DAYS_FOR_DEPRECATION == 30
    # exactly at the threshold is trusted enough to keep ACTIVE
    boundary = status_decision(
        current=MemoryStatus.ACTIVE,
        hit_count=1,
        corrected_count=1,
        last_hit_at=None,
        now=datetime(2026, 9, 5),
    )
    assert boundary.status is MemoryStatus.ACTIVE
    assert TRUST_PENDING_THRESHOLD == 0.5


# --- status demotion: DB sweep ------------------------------------------------


async def test_sweep_status_applies_fixture_table_end_to_end(storage: SqliteStorage) -> None:
    config = _load("demotion.json")
    now = _parse_datetime(config["now"])
    seeded = [
        await _seed_parse_memory(
            storage,
            status=MemoryStatus(case["status"]),
            hit_count=case["hit_count"],
            corrected_count=case["corrected_count"],
            last_hit_at=_parse_datetime(case["last_hit_at"]) if case["last_hit_at"] else None,
        )
        for case in config["cases"]
    ]

    governance = MemoryGovernance(storage)
    report = await governance.sweep_status(
        no_hit_days_for_deprecation=config["no_hit_days_for_deprecation"],
        now=now,
        operation_id="op-sweep-1",
    )

    assert report.operation_id == "op-sweep-1"
    assert report.demoted_to_pending == 1
    assert report.deprecated == 2
    assert report.unchanged == 3

    for row, case in zip(seeded, config["cases"], strict=True):
        persisted = await storage.get(ParseMemory, row.id)
        assert persisted is not None
        assert persisted.status is MemoryStatus(case["expected_status"]), case["name"]


async def test_sweep_status_writes_one_audit_batch(storage: SqliteStorage) -> None:
    config = _load("demotion.json")
    now = _parse_datetime(config["now"])
    for case in config["cases"]:
        await _seed_parse_memory(
            storage,
            status=MemoryStatus(case["status"]),
            hit_count=case["hit_count"],
            corrected_count=case["corrected_count"],
            last_hit_at=_parse_datetime(case["last_hit_at"]) if case["last_hit_at"] else None,
        )

    governance = MemoryGovernance(storage)
    report = await governance.sweep_status(now=now)

    audits = await storage.list(AuditLog)
    assert len(audits) == report.demoted_to_pending + report.deprecated
    assert {audit.operation_id for audit in audits} == {report.operation_id}
    actions = {audit.action for audit in audits}
    assert actions == {ACTION_DEMOTE_PENDING, ACTION_DEPRECATE}
    for audit in audits:
        assert audit.entity == ENTITY_PARSE_MEMORY
        assert audit.actor is Actor.AUTO
        assert audit.reverse["status"] in {"active", "pending"}
        assert "trust" in audit.instruction


async def test_sweep_status_is_idempotent_once_settled(storage: SqliteStorage) -> None:
    config = _load("demotion.json")
    now = _parse_datetime(config["now"])
    for case in config["cases"]:
        await _seed_parse_memory(
            storage,
            status=MemoryStatus(case["status"]),
            hit_count=case["hit_count"],
            corrected_count=case["corrected_count"],
            last_hit_at=_parse_datetime(case["last_hit_at"]) if case["last_hit_at"] else None,
        )

    governance = MemoryGovernance(storage)
    await governance.sweep_status(now=now, operation_id="op-1")
    second = await governance.sweep_status(now=now, operation_id="op-2")

    assert second.demoted_to_pending == 0
    assert second.deprecated == 0
    assert second.unchanged == len(config["cases"])


async def test_deprecated_never_revives_even_after_recovery(storage: SqliteStorage) -> None:
    row = await _seed_parse_memory(
        storage,
        status=MemoryStatus.DEPRECATED,
        hit_count=100,
        corrected_count=0,
        last_hit_at=None,
    )

    governance = MemoryGovernance(storage)
    report = await governance.sweep_status(now=datetime(2026, 9, 5))

    persisted = await storage.get(ParseMemory, row.id)
    assert persisted is not None
    assert persisted.status is MemoryStatus.DEPRECATED
    assert report.unchanged == 1


# --- audit writes ---------------------------------------------------------------


async def test_record_audit_defaults_and_batch_field(
    storage: SqliteStorage, governance: MemoryGovernance
) -> None:
    row = await governance.record_audit(
        operation_id="op-batch-42",
        entity=ENTITY_BYPASS_LIST,
        action=ACTION_BYPASS_ADD,
        entity_id=7,
        instruction={"pattern_hash": "abc"},
    )

    assert row.operation_id == "op-batch-42"
    assert row.entity_id == 7
    assert row.actor is Actor.AUTO
    assert row.reverse == {}
    persisted = await storage.get(AuditLog, row.id)
    assert persisted is not None
    assert persisted.instruction == {"pattern_hash": "abc"}


async def test_record_memory_hit_audit_uses_parse_memory_entity(
    governance: MemoryGovernance,
) -> None:
    row = await governance.record_memory_hit_audit(
        operation_id="op-hit-1",
        entity_id=3,
        instruction={"key_level": 1, "trust": 0.9},
    )

    assert row.entity == ENTITY_PARSE_MEMORY
    assert row.action == ACTION_MEMORY_HIT
    assert row.entity_id == 3


async def test_hit_and_demotion_events_share_one_operation_id(
    storage: SqliteStorage, governance: MemoryGovernance
) -> None:
    await _seed_parse_memory(
        storage,
        status=MemoryStatus.ACTIVE,
        hit_count=0,
        corrected_count=1,
        last_hit_at=None,
    )
    report = await governance.sweep_status(now=datetime(2026, 9, 5), operation_id="shared-op")
    hit = await governance.record_memory_hit_audit(
        operation_id=report.operation_id, entity_id=1
    )

    assert hit.operation_id == "shared-op"
    audits = await storage.list(AuditLog)
    assert {audit.operation_id for audit in audits} == {"shared-op"}


# --- governance stores a Series FK context for alias rows ---------------------


async def test_alias_resolves_against_seeded_series(
    storage: SqliteStorage, alias_service: AliasService
) -> None:
    series = Series(title_jp="葬送のフリーレン")
    await storage.add(series)
    await alias_service.add_alias(series.id, "葬送的芙莉莲")

    assert await alias_service.find_series_id("葬送的芙莉莲") == series.id
    assert await alias_service.find_series_id("Sousou no Frieren") is None
