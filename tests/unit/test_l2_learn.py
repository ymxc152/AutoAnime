"""Unit tests for the L2 learning/write side (PR4 T2).

Covers the learn path end to end on an in-memory SQLite database: confirmed
result -> bypass gate -> two-level upsert -> read-back, driven by the learn
roundtrip fixtures under ``tests/fixtures/memory/roundtrip/learn/`` plus
in-code cases for correction/no-op/bypass semantics and the CLI confirm
subcommand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoanime import cli
from autoanime.config import Settings
from autoanime.core.enums import Confidence, MemorySource, MemoryStatus, Segment
from autoanime.core.interfaces import ParseResult
from autoanime.core.models import BypassList, ParseMemory
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.learn import (
    BypassLookup,
    MemoryWriteStore,
    StorageMemoryAccess,
    derive_memory_key,
    learn_confirmation,
    status_for_counts,
    stored_result_for,
    upsert_parse_memory,
)
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l2 import (
    KEY_LEVEL_EXACT,
    KEY_LEVEL_SERIES,
    key_hash,
    level1_key,
    level2_key,
    pattern_hash,
)

LEARN_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "memory" / "roundtrip" / "learn"
_MEMORY_DB = "sqlite+aiosqlite:///:memory:"


def _load_learn_fixtures() -> list[dict[str, Any]]:
    paths = sorted(LEARN_FIXTURE_ROOT.glob("*.json"))
    assert paths, "learn roundtrip fixtures are missing"
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def _str_field(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    assert isinstance(value, str), f"fixture field '{key}' must be a string"
    return value


def _optional_str_field(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    assert isinstance(value, str), f"fixture field '{key}' must be a string or null"
    return value


def _optional_int_field(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


class _FakeBypass:
    """BypassLookup fake: at most one listed digest, records every query."""

    def __init__(self, digest: str | None = None) -> None:
        self.digest = digest
        self.seen: list[str] = []

    async def has_bypass(self, pattern_hash: str) -> bool:
        self.seen.append(pattern_hash)
        return self.digest is not None and pattern_hash == self.digest


def _confirmed(payload: dict[str, Any]) -> ParseResult:
    """Build a confirmed ParseResult from a fixture confirmed-shaped object."""
    return ParseResult(
        title=_str_field(payload, "title"),
        season=_optional_int_field(payload, "season"),
        episode=_optional_int_field(payload, "episode"),
        segment=Segment(_str_field(payload, "segment")),
        fansub=_optional_str_field(payload, "fansub"),
        level=Confidence(_str_field(payload, "level")),
        confidence=float(payload["confidence"]),
        missing_fields=tuple(payload.get("missing_fields", ())),
        evidence=dict(payload.get("evidence", {})),
    )


def _key_from_stored_result(result: dict[str, Any], key_level: int) -> str:
    """Recompute the canonical key text from a stored result dict alone."""
    assert isinstance(result["title"], str)
    title = result["title"]
    season = result.get("season")
    episode = result.get("episode")
    fansub = result.get("fansub")
    season = season if isinstance(season, int) and not isinstance(season, bool) else None
    episode = episode if isinstance(episode, int) and not isinstance(episode, bool) else None
    fansub = fansub if isinstance(fansub, str) else None
    if key_level == KEY_LEVEL_SERIES:
        return level1_key(title)
    if key_level == KEY_LEVEL_EXACT:
        return level2_key(title, season, episode, fansub)
    raise AssertionError(f"unexpected key level in fixture: {key_level}")


def _assert_rows_match(rows: list[Any], expected: list[dict[str, Any]]) -> None:
    """Match stored rows against expected entries by (key_level, result)."""
    remaining = list(rows)
    for item in expected:
        key_level = item["key_level"]
        want_result = dict(item["result"])
        match = next(
            (
                row
                for row in remaining
                if row.key_level == key_level and dict(row.result or {}) == want_result
            ),
            None,
        )
        assert match is not None, f"no stored row for level {key_level} result {want_result}"
        remaining.remove(match)

        assert match.hit_count == item["hit_count"]
        assert match.corrected_count == item["corrected_count"]
        assert MemoryStatus(match.status) is MemoryStatus(item["status"])
        assert match.title_shape == item["title_shape"]
        assert match.fansub_norm == item["fansub_norm"]
        # key_hash stability: recomputable from the stored result alone.
        assert match.key_hash == key_hash(_key_from_stored_result(want_result, key_level))
    assert not remaining, f"unexpected stored rows: {[(r.key_level, r.result) for r in remaining]}"


# --- learn roundtrip fixtures (in-memory SQLite: learn then read back) ------


@pytest.mark.parametrize(
    "payload", _load_learn_fixtures(), ids=lambda payload: str(payload["id"])
)
async def test_learn_roundtrip_fixture(payload: dict[str, Any]) -> None:
    confirmed = _confirmed(payload["confirmed"])
    source = MemorySource(_str_field(payload, "source"))
    bypass_digest = pattern_hash(_str_field(payload, "raw_name")) if payload["bypassed"] else None
    bypass = _FakeBypass(bypass_digest)

    async with SqliteStorage(_MEMORY_DB) as storage:
        access = StorageMemoryAccess(storage)
        for _ in range(int(payload.get("repeat", 1))):
            outcome = await learn_confirmation(
                access,
                confirmed=confirmed,
                raw_name=_str_field(payload, "raw_name"),
                source=source,
                bypass_lookup=bypass,
            )
        for correction in payload.get("corrections", []):
            assert isinstance(correction, dict)
            outcome = await learn_confirmation(
                access,
                confirmed=_confirmed(correction),
                raw_name=_str_field(payload, "raw_name"),
                source=source,
                bypass_lookup=bypass,
            )
        rows = await storage.list(ParseMemory)

    assert outcome.bypassed is bool(payload["bypassed"])
    if payload["bypassed"]:
        assert rows == []
        assert outcome.entries == ()
        assert bypass.seen == [pattern_hash(_str_field(payload, "raw_name"))]
    else:
        assert outcome.bypassed is False
        assert all(MemorySource(row.source) is source for row in rows)
        _assert_rows_match(rows, payload["expected"])


# --- pure helpers -----------------------------------------------------------


def test_series_stored_result_never_carries_an_episode() -> None:
    confirmed = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": 5,
            "segment": "episode",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    series = stored_result_for(confirmed, key_level=KEY_LEVEL_SERIES)
    exact = stored_result_for(confirmed, key_level=KEY_LEVEL_EXACT)

    assert series["episode"] is None
    assert series["seasons"] == [1]
    assert series["segment"] == "episode"
    assert exact["episode"] == 5
    assert exact["fansub"] == "MWeb"


def test_derive_memory_key_matches_t1_levels() -> None:
    confirmed = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": 5,
            "segment": "episode",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    assert derive_memory_key(confirmed, key_level=KEY_LEVEL_SERIES) == "some show"
    assert derive_memory_key(confirmed, key_level=KEY_LEVEL_EXACT) == (
        "some show|s=1|e=5|f=mweb"
    )


@pytest.mark.parametrize(
    ("hit_count", "corrected_count", "expected"),
    [
        (0, 0, MemoryStatus.ACTIVE),
        (1, 0, MemoryStatus.ACTIVE),
        (3, 1, MemoryStatus.ACTIVE),
        (0, 1, MemoryStatus.PENDING),
        (1, 2, MemoryStatus.PENDING),
    ],
)
def test_status_for_counts_pins_the_trust_thresholds(
    hit_count: int, corrected_count: int, expected: MemoryStatus
) -> None:
    assert status_for_counts(hit_count, corrected_count) is expected


def test_unknown_key_level_is_rejected() -> None:
    confirmed = _confirmed(
        {
            "title": "Some Show",
            "season": None,
            "episode": None,
            "segment": "episode",
            "fansub": None,
            "level": "high",
            "confidence": 1.0,
        }
    )
    with pytest.raises(ValueError, match="unknown key level"):
        stored_result_for(confirmed, key_level=3)
    with pytest.raises(ValueError, match="unknown key level"):
        derive_memory_key(confirmed, key_level=3)


# --- upsert semantics on a real in-memory SQLite store ----------------------


async def test_learn_writes_both_levels_with_stable_hashes() -> None:
    confirmed = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": 5,
            "segment": "episode",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    async with SqliteStorage(_MEMORY_DB) as storage:
        outcome = await learn_confirmation(
            StorageMemoryAccess(storage),
            confirmed=confirmed,
            raw_name="Some.Show.S01E05.Baha.1080p.mkv",
            source=MemorySource.LLM_CONFIRMED,
            bypass_lookup=_FakeBypass(),
        )
        rows = await storage.list(ParseMemory)

    assert not outcome.bypassed
    assert len(rows) == 2
    by_level = {row.key_level: row for row in rows}
    assert by_level[KEY_LEVEL_SERIES].key_hash == key_hash(level1_key("Some Show"))
    assert by_level[KEY_LEVEL_EXACT].key_hash == key_hash(
        level2_key("Some Show", 1, 5, "MWeb")
    )
    assert by_level[KEY_LEVEL_SERIES].title_shape == "some show"
    assert by_level[KEY_LEVEL_SERIES].fansub_norm == "mweb"
    assert by_level[KEY_LEVEL_SERIES].status is MemoryStatus.ACTIVE
    assert MemorySource(by_level[KEY_LEVEL_EXACT].source) is MemorySource.LLM_CONFIRMED


async def test_relearn_identical_result_is_a_noop() -> None:
    confirmed = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": 5,
            "segment": "episode",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    async with SqliteStorage(_MEMORY_DB) as storage:
        access = StorageMemoryAccess(storage)
        for _ in range(3):
            await learn_confirmation(
                access,
                confirmed=confirmed,
                raw_name="Some.Show.S01E05.Baha.1080p.mkv",
                bypass_lookup=_FakeBypass(),
            )
        rows = await storage.list(ParseMemory)

    assert len(rows) == 2
    assert all(row.hit_count == 0 and row.corrected_count == 0 for row in rows)
    assert all(row.status is MemoryStatus.ACTIVE for row in rows)




async def test_series_level_variant_is_not_a_correction() -> None:
    season_pack = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": None,
            "segment": "season_pack",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    episode = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": 2,
            "segment": "episode",
            "fansub": "OtherGroup",
            "level": "high",
            "confidence": 1.0,
        }
    )
    async with SqliteStorage(_MEMORY_DB) as storage:
        access = StorageMemoryAccess(storage)
        await learn_confirmation(
            access, confirmed=season_pack, raw_name="Some.Show.S01-MWeb", bypass_lookup=_FakeBypass()
        )
        await learn_confirmation(
            access, confirmed=episode, raw_name="Some.Show.S01E02.mkv", bypass_lookup=_FakeBypass()
        )
        rows = await storage.list(ParseMemory)

    series = next(row for row in rows if row.key_level == KEY_LEVEL_SERIES)
    assert series.corrected_count == 0
    assert series.hit_count == 0
    assert series.status is MemoryStatus.ACTIVE
    # Completeness and legal series variants never poison the workhorse row.
    assert dict(series.result)["seasons"] == [1]
    assert dict(series.result)["segment"] == "season_pack"
    assert dict(series.result)["fansub"] == "MWeb"


async def test_segment_correction_on_same_exact_key_replaces_result_and_counts() -> None:
    first = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": 5,
            "segment": "episode",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    # Same exact key: only the non-key segment field disagrees.
    corrected = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": 5,
            "segment": "season_pack",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    async with SqliteStorage(_MEMORY_DB) as storage:
        access = StorageMemoryAccess(storage)
        await learn_confirmation(
            access, confirmed=first, raw_name="Some.Show.S01E05.mkv", bypass_lookup=_FakeBypass()
        )
        await learn_confirmation(
            access,
            confirmed=corrected,
            raw_name="Some.Show.S01E05.mkv",
            source=MemorySource.MANUAL,
            bypass_lookup=_FakeBypass(),
        )
        rows = await storage.list(ParseMemory)

    series_rows = [row for row in rows if row.key_level == KEY_LEVEL_SERIES]
    exact_rows = [row for row in rows if row.key_level == KEY_LEVEL_EXACT]
    assert all(row.corrected_count == 0 and row.status is MemoryStatus.ACTIVE for row in series_rows)
    assert len(exact_rows) == 1
    assert exact_rows[0].corrected_count == 1
    assert exact_rows[0].hit_count == 0
    # trust = 0/(0+1) = 0.0 < 0.5 -> PENDING
    assert exact_rows[0].status is MemoryStatus.PENDING
    assert dict(exact_rows[0].result)["segment"] == "season_pack"


async def test_correction_keeps_deprecated_entry_terminal() -> None:
    first = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": 5,
            "segment": "episode",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    corrected = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": 5,
            "segment": "season_pack",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    async with SqliteStorage(_MEMORY_DB) as storage:
        access = StorageMemoryAccess(storage)
        await learn_confirmation(
            access, confirmed=first, raw_name="Some.Show.S01E05.mkv", bypass_lookup=_FakeBypass()
        )
        await learn_confirmation(
            access,
            confirmed=corrected,
            raw_name="Some.Show.S01E05.mkv",
            bypass_lookup=_FakeBypass(),
        )
        # trust = 0 -> PENDING; a sweep with no hits deprecates the exact entry
        # (the series row saw a no-op re-confirm and stays ACTIVE).
        report = await MemoryGovernance(storage).sweep_status()
        assert report.deprecated == 1
        rows = await storage.list(ParseMemory)
        assert all(
            row.status is (MemoryStatus.DEPRECATED if row.key_level == KEY_LEVEL_EXACT else MemoryStatus.ACTIVE)
            for row in rows
        )

        # Re-confirming with another conflict never resurrects DEPRECATED.
        reconfirmed = _confirmed(
            {
                "title": "Some Show",
                "season": 1,
                "episode": 5,
                "segment": "movie",
                "fansub": "MWeb",
                "level": "high",
                "confidence": 1.0,
            }
        )
        await learn_confirmation(
            access,
            confirmed=reconfirmed,
            raw_name="Some.Show.S01E05.mkv",
            bypass_lookup=_FakeBypass(),
        )
        rows = await storage.list(ParseMemory)

    assert all(row.status is MemoryStatus.DEPRECATED for row in rows if row.key_level == KEY_LEVEL_EXACT)
    exact = next(row for row in rows if row.key_level == KEY_LEVEL_EXACT)
    assert exact.corrected_count == 2
    assert dict(exact.result)["segment"] == "movie"


async def test_correction_changing_exact_key_inserts_new_row() -> None:
    first = _confirmed(
        {
            "title": "Some Show",
            "season": 1,
            "episode": 5,
            "segment": "episode",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    corrected = _confirmed(
        {
            "title": "Some Show",
            "season": 2,
            "episode": 5,
            "segment": "episode",
            "fansub": "MWeb",
            "level": "high",
            "confidence": 1.0,
        }
    )
    async with SqliteStorage(_MEMORY_DB) as storage:
        access = StorageMemoryAccess(storage)
        await learn_confirmation(
            access, confirmed=first, raw_name="Some.Show.S01E05.mkv", bypass_lookup=_FakeBypass()
        )
        await learn_confirmation(
            access,
            confirmed=corrected,
            raw_name="Some.Show.S02E05.mkv",
            bypass_lookup=_FakeBypass(),
        )
        rows = await storage.list(ParseMemory)

    series_rows = [row for row in rows if row.key_level == KEY_LEVEL_SERIES]
    exact_rows = [row for row in rows if row.key_level == KEY_LEVEL_EXACT]
    # Series key only depends on the title: a differing season is a legal
    # multi-season observation, merged into the seasons list (no correction).
    assert len(series_rows) == 1
    assert series_rows[0].corrected_count == 0
    assert series_rows[0].status is MemoryStatus.ACTIVE
    assert dict(series_rows[0].result)["seasons"] == [1, 2]
    # Exact key moved with the season: the old entry stays, the new one is fresh.
    assert len(exact_rows) == 2
    assert {dict(row.result)["season"] for row in exact_rows} == {1, 2}
    assert all(row.corrected_count == 0 and row.status is MemoryStatus.ACTIVE for row in exact_rows)


async def test_bypassed_raw_name_is_never_written() -> None:
    confirmed = _confirmed(
        {
            "title": "Noisy Release",
            "season": 1,
            "episode": 1,
            "segment": "episode",
            "fansub": "Group",
            "level": "high",
            "confidence": 1.0,
        }
    )
    raw_name = "Noisy.Release.S01E01.Group.mkv"
    bypass = _FakeBypass(pattern_hash(raw_name))
    async with SqliteStorage(_MEMORY_DB) as storage:
        outcome = await learn_confirmation(
            StorageMemoryAccess(storage),
            confirmed=confirmed,
            raw_name=raw_name,
            bypass_lookup=bypass,
        )
        rows = await storage.list(ParseMemory)

    assert outcome.bypassed is True
    assert outcome.entries == ()
    assert rows == []
    assert bypass.seen == [pattern_hash(raw_name)]


async def test_storage_memory_access_has_bypass_reads_bypass_list() -> None:
    async with SqliteStorage(_MEMORY_DB) as storage:
        access = StorageMemoryAccess(storage)
        listed = pattern_hash("Noisy.Release.S01E01.Group.mkv")
        await storage.add(BypassList(pattern_hash=listed, reason="noise"))

        assert await access.has_bypass(listed) is True
        assert await access.has_bypass(pattern_hash("Other.Show.S01E01")) is False


async def test_upsert_parse_memory_inserts_then_updates_in_place() -> None:
    confirmed = _confirmed(
        {
            "title": "Some Show",
            "season": None,
            "episode": None,
            "segment": "episode",
            "fansub": None,
            "level": "high",
            "confidence": 1.0,
        }
    )
    async with SqliteStorage(_MEMORY_DB) as storage:
        access = StorageMemoryAccess(storage)
        inserted = await upsert_parse_memory(
            access, confirmed=confirmed, key_level=KEY_LEVEL_SERIES, source=MemorySource.MANUAL
        )
        updated = await upsert_parse_memory(
            access, confirmed=confirmed, key_level=KEY_LEVEL_SERIES, source=MemorySource.MANUAL
        )

        assert updated.id == inserted.id
        assert len(await storage.list(ParseMemory)) == 1


def test_storage_memory_access_satisfies_learn_protocols() -> None:
    storage = SqliteStorage(_MEMORY_DB)
    access = StorageMemoryAccess(storage)
    assert isinstance(access, MemoryWriteStore)
    assert isinstance(access, BypassLookup)


# --- CLI confirm subcommand -------------------------------------------------


async def _read_rows(database_url: str) -> list[Any]:
    async with SqliteStorage(database_url) as storage:
        return await storage.list(ParseMemory)


def _patch_settings(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(database_url=database_url))


def test_cli_confirm_uses_l1_draft_defaults_and_writes_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'autoanime.db'}"
    _patch_settings(monkeypatch, database_url)

    rc = cli.main(["confirm", "--name", "[MWeb] Some Show - 05 [1080p].mkv"])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["bypassed"] is False
    assert {entry["key_level"] for entry in out["entries"]} == {1, 2}

    rows = asyncio.run(_read_rows(database_url))
    assert len(rows) == 2
    series = next(row for row in rows if row.key_level == KEY_LEVEL_SERIES)
    assert series.title_shape == "some show"
    assert dict(series.result) == {
        "title": "Some Show",
        "seasons": [],
        "episode": None,
        "segment": "episode",
        "fansub": "MWeb",
    }
    assert series.status is MemoryStatus.ACTIVE


def test_cli_confirm_explicit_flags_override_draft_and_flow_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'autoanime.db'}"
    _patch_settings(monkeypatch, database_url)

    rc = cli.main(
        [
            "confirm",
            "--name", "garbage.mkv",
            "--title", "Manual Show",
            "--season", "2",
            "--episode", "7",
            "--segment", "episode",
            "--fansub", "MWeb",
            "--source", "llm_auto",
        ]
    )

    assert rc == 0
    json.loads(capsys.readouterr().out)
    rows = asyncio.run(_read_rows(database_url))
    assert len(rows) == 2
    exact = next(row for row in rows if row.key_level == KEY_LEVEL_EXACT)
    assert exact.key_hash == key_hash(level2_key("Manual Show", 2, 7, "MWeb"))
    assert MemorySource(exact.source) is MemorySource.LLM_AUTO


def test_cli_confirm_without_title_fails_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'autoanime.db'}"
    _patch_settings(monkeypatch, database_url)

    rc = cli.main(["confirm", "--name", "garbage.mkv"])

    assert rc == 2
    assert "confirm:" in capsys.readouterr().out
    assert asyncio.run(_read_rows(database_url)) == []


def test_cli_confirm_bypassed_name_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'autoanime.db'}"
    raw_name = "Noisy.Release.S01E01.Group.mkv"

    async def _seed_bypass() -> None:
        async with SqliteStorage(database_url) as storage:
            await storage.add(
                BypassList(pattern_hash=pattern_hash(raw_name), reason="noise")
            )

    asyncio.run(_seed_bypass())
    _patch_settings(monkeypatch, database_url)

    rc = cli.main(["confirm", "--name", raw_name, "--title", "Noisy Release"])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"bypassed": True, "entries": []}
    assert asyncio.run(_read_rows(database_url)) == []
