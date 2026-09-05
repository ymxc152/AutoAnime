"""scheduler.library_reconcile 单测（E4b，B5）：文件存在性对账 + 积压告警。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from autoanime.config import Settings
from autoanime.core.enums import EpisodeState, MediaType, ReleaseStatus
from autoanime.core.events import Event
from autoanime.core.models import Episode, PendingQueue, ReleaseRecord, Season, Series
from autoanime.memory.store import SqliteStorage
from autoanime.scheduler.library_reconcile import LibraryReconciler
from autoanime.scheduler.store import LoopStore

NOW = datetime(2026, 9, 6, 12, 0, 0)
_OPEN_STORAGES: list[SqliteStorage] = []


@pytest.fixture(autouse=True)
async def _close_storages() -> Any:
    yield
    for storage in _OPEN_STORAGES:
        await storage.close()
    _OPEN_STORAGES.clear()


class BusRecorder:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)


async def make_rig(tmp_path: Path, *, existing: bool, pending_rows: int = 0) -> tuple[LoopStore, SqliteStorage, LibraryReconciler, BusRecorder]:
    storage = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await storage.create_all()
    _OPEN_STORAGES.append(storage)
    store = LoopStore(storage)
    series = await store.create_subscription(
        Series(title_cn="孤独摇滚", media_type=MediaType.TV, status="active"),
        Season(number=1),
        [Episode(number=1, state=EpisodeState.MISSING)],
    )
    season = (await store.seasons_for_series(series.id))[0]
    episode = await store.episode_for_number(season.id, 1)
    assert episode is not None
    for state in (EpisodeState.DOWNLOADING, EpisodeState.DOWNLOADED, EpisodeState.ORGANIZED):
        await store.transition_episode(episode.id, state)
    file_path = tmp_path / "library" / "e01.mkv"
    if existing:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"v")
    episode.file_path = str(file_path)
    await storage.add(episode)
    for _ in range(pending_rows):
        await store.add_pending(PendingQueue(raw_name="x", stage="mismatch"))
    settings = Settings(pending_backlog_alert_threshold=2)
    bus = BusRecorder()
    return store, storage, LibraryReconciler(store, settings, bus=bus), bus


async def test_missing_file_flags_episode(tmp_path: Path) -> None:
    store, storage, reconciler, bus = await make_rig(tmp_path, existing=False)
    report = await reconciler.reconcile(now=NOW)
    assert report.checked == 1
    assert report.flagged == 1
    season_id = (await store.seasons_for_series(1))[0].id
    episode = await store.episode_for_number(season_id, 1)
    assert episode is not None
    state = episode.state.value if hasattr(episode.state, "value") else episode.state
    assert state == "flagged"  # B5：只标记 + 通知，不自动修
    assert any(event.message == "episode.flagged" for event in bus.events)


async def test_existing_file_not_flagged(tmp_path: Path) -> None:
    store, storage, reconciler, bus = await make_rig(tmp_path, existing=True)
    report = await reconciler.reconcile(now=NOW)
    assert report.flagged == 0
    assert bus.events == []


async def test_pending_backlog_alert(tmp_path: Path) -> None:
    _store, _storage, reconciler, bus = await make_rig(tmp_path, existing=True, pending_rows=5)
    report = await reconciler.reconcile(now=NOW)
    assert report.pending_backlog == 5
    assert report.backlog_alert is True  # 阈值 2
    assert any(event.message == "pending.backlog" for event in bus.events)


async def test_release_status_lifecycle_roundtrip(tmp_path: Path) -> None:
    """B4 补扫口径自检：completed 状态可被按状态列出（对账输入）。"""
    store, storage, _reconciler, _bus = await make_rig(tmp_path, existing=True)
    record = ReleaseRecord(episode_id=1, torrent_hash="c" * 40)
    saved = await store.create_release(record)
    assert saved is not None
    for state in (ReleaseStatus.PICKED, ReleaseStatus.DOWNLOADING, ReleaseStatus.COMPLETED):
        await store.transition_release(saved.id, state, now=NOW)
    done = await store.list_releases_by_status([ReleaseStatus.COMPLETED])
    assert [row.torrent_hash for row in done] == ["c" * 40]
