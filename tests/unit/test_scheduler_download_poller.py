"""scheduler.download_poller 单测（E4a）：轮询比对/完成回调/重试上界/补扫。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from autoanime.core.enums import (
    Decision,
    EpisodeState,
    MediaType,
    ReleaseStatus,
)
from autoanime.core.models import Episode, ReleaseRecord, Season, Series
from autoanime.gateway.torrents import bencode, torrent_info_hash
from autoanime.memory.store import SqliteStorage
from autoanime.scheduler.download_poller import DownloadPoller
from autoanime.scheduler.store import LoopStore

NOW = datetime(2026, 9, 6, 12, 0, 0)

_OPEN_STORAGES: list[SqliteStorage] = []


@pytest.fixture(autouse=True)
async def _close_storages() -> Any:
    yield
    for storage in _OPEN_STORAGES:
        await storage.close()
    _OPEN_STORAGES.clear()


class FakeGateway:
    """可控网关：按哈希注入 qB 风格状态。"""

    def __init__(self) -> None:
        self.statuses: dict[str, dict[str, object] | None] = {}
        self.added: list[bytes] = []

    async def add_torrent_bytes(self, data: bytes, *, save_path: str | None = None) -> str:
        self.added.append(data)
        return torrent_info_hash(data)

    async def status(self, torrent_hash: str) -> dict[str, object] | None:
        return self.statuses.get(torrent_hash)

    async def completed_hashes(self) -> list[str]:
        return [
            torrent_hash
            for torrent_hash, row in self.statuses.items()
            if row is not None and row.get("progress") == 1.0
        ]

    async def files(self, torrent_hash: str) -> list[dict[str, object]]:
        return [{"name": "Show - 01.mkv", "size": 1}]


class Rig:
    def __init__(
        self,
        store: LoopStore,
        storage: SqliteStorage,
        gateway: FakeGateway,
        poller: DownloadPoller,
        release: ReleaseRecord,
        episode: Episode,
    ) -> None:
        self.store = store
        self.storage = storage
        self.gateway = gateway
        self.poller = poller
        self.release = release
        self.episode = episode


async def make_rig(
    *,
    status: dict[str, object] | None = None,
    max_retries: int = 2,
    refetch_data: bytes | None = bencode({"info": {"name": "Show - 01", "length": 1}}),
    on_completed: Any | None = None,
    release_status: ReleaseStatus = ReleaseStatus.DOWNLOADING,
) -> Rig:
    storage = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await storage.create_all()
    _OPEN_STORAGES.append(storage)
    store = LoopStore(storage)
    series = await store.create_subscription(
        Series(title_cn="孤独摇滚", media_type=MediaType.TV, status="active"),
        Season(number=1),
        [Episode(number=1, state=EpisodeState.DOWNLOADING)],
    )
    season = (await store.seasons_for_series(series.id))[0]
    episode = (await store.episodes_for_season(season.id))[0]
    data = bencode({"info": {"name": "Show - 01", "length": 1}})
    infohash = torrent_info_hash(data)
    record = ReleaseRecord(episode_id=episode.id, torrent_hash=infohash, source_url="u")
    await store.create_release(record)
    await store.transition_release(record.id, ReleaseStatus.PICKED, now=NOW, decision=Decision.ACCEPTED)
    if release_status is ReleaseStatus.DOWNLOADING:
        await store.transition_release(record.id, ReleaseStatus.DOWNLOADING, now=NOW)
    gateway = FakeGateway()
    if status is not None:
        gateway.statuses[infohash] = status
    async def _refetch(_url: str) -> bytes | None:
        return refetch_data
    poller = DownloadPoller(
        store,
        gateway,
        max_retries=max_retries,
        on_completed=on_completed,
        torrent_refetch=_refetch,
    )
    release = (await store.list_releases_by_status([release_status]))[0]
    return Rig(store, storage, gateway, poller, release, episode)


async def test_completed_transitions_and_callback() -> None:
    seen: list[tuple[str, list[dict[str, object]]]] = []

    async def on_completed(release: ReleaseRecord, files: list[dict[str, object]]) -> None:
        seen.append((release.torrent_hash, files))

    rig = await make_rig(
        status={"state": "downloading", "progress": 1.0, "name": "Show - 01"},
        on_completed=on_completed,
    )
    report = await rig.poller.poll_once(now=NOW)
    assert report.completed == 1
    assert len(seen) == 1
    assert seen[0][0] == rig.release.torrent_hash
    assert seen[0][1] == [{"name": "Show - 01.mkv", "size": 1}]
    refreshed = await rig.store.get_episode(rig.episode.id)
    assert refreshed is not None and refreshed.state == EpisodeState.DOWNLOADED
    done = await rig.store.list_releases_by_status([ReleaseStatus.COMPLETED])
    assert done[0].finished_at is not None


async def test_error_state_retries_then_fails() -> None:
    rig = await make_rig(
        status={"state": "error", "progress": 0.3, "name": "Show - 01"},
        max_retries=2,
    )
    first = await rig.poller.poll_once(now=NOW)
    assert first.retried == 1
    picked = await rig.store.list_releases_by_status([ReleaseStatus.PICKED])
    assert len(picked) == 1  # failed → picked（重试窗口内）
    second = await rig.poller.poll_once(now=NOW)
    assert second.retried == 1
    third = await rig.poller.poll_once(now=NOW)
    assert third.failed == 1  # 重试上界 2 用尽
    failed = await rig.store.list_releases_by_status([ReleaseStatus.FAILED])
    assert len(failed) == 1
    assert "retries exhausted" in (failed[0].reason or "")


async def test_missing_hash_with_refetch_resubmits() -> None:
    fresh = bencode({"info": {"name": "Show - 01", "length": 1}})
    rig = await make_rig(status=None, max_retries=2, refetch_data=fresh)
    report = await rig.poller.poll_once(now=NOW)
    assert report.retried == 1
    assert rig.gateway.added == [fresh]


async def test_missing_hash_without_refetch_fails_fast() -> None:
    rig = await make_rig(status=None, max_retries=2, refetch_data=None)
    report = await rig.poller.poll_once(now=NOW)
    assert report.failed == 1
    failed = await rig.store.list_releases_by_status([ReleaseStatus.FAILED])
    assert "refetch failed" in (failed[0].reason or "")


async def test_reconcile_startup_flags_suspended_completed() -> None:
    """A4/B4：完成但未归档的悬挂任务（两次转移间崩溃）补扫幂等恢复。"""
    rig = await make_rig(
        status={"state": "downloading", "progress": 1.0, "name": "Show - 01"},
    )
    # 直接把 release 置 COMPLETED，episode 留在 DOWNLOADING（模拟崩溃间隙）
    await rig.store.transition_release(rig.release.id, ReleaseStatus.COMPLETED, now=NOW)
    report = await rig.poller.reconcile_startup(now=NOW)
    assert report.reconciled == 1
    refreshed = await rig.store.get_episode(rig.episode.id)
    assert refreshed is not None and refreshed.state == EpisodeState.DOWNLOADED
    # 幂等：再跑一轮不再重复计数
    again = await rig.poller.reconcile_startup(now=NOW)
    assert again.reconciled == 0


async def test_reconcile_notes_untracked_completed_hashes() -> None:
    rig = await make_rig(status=None)
    other = torrent_info_hash(bencode({"info": {"name": "other", "length": 1}}))
    rig.gateway.statuses[other] = {"state": "uploading", "progress": 1.0}
    report = await rig.poller.reconcile_startup(now=NOW)
    assert any(other in note for note in report.notes)


async def test_completed_on_first_poll_from_picked() -> None:
    """回归（R1 验收）：网关在首次采样前已完成（release 仍 picked）。

    状态机不允许 picked → completed 直达；修复前该转移抛 TransitionError
    被轮询器吞成 note，任务永远卡死在 picked、episode 永不 DOWNLOADED。
    修复：完成路径先补 picked → downloading 一跳。
    """
    seen: list[str] = []

    async def on_completed(release: ReleaseRecord, files: list[dict[str, object]]) -> None:
        seen.append(release.torrent_hash)

    rig = await make_rig(
        status={"state": "pausedUP", "progress": 1.0, "name": "Show - 01"},
        release_status=ReleaseStatus.PICKED,
        on_completed=on_completed,
    )
    report = await rig.poller.poll_once(now=NOW)
    assert report.completed == 1
    assert report.notes == ()
    assert seen == [rig.release.torrent_hash]
    refreshed = await rig.store.get_episode(rig.episode.id)
    assert refreshed is not None and refreshed.state == EpisodeState.DOWNLOADED
    done = await rig.store.list_releases_by_status([ReleaseStatus.COMPLETED])
    assert [row.torrent_hash for row in done] == [rig.release.torrent_hash]
