"""scheduler.store 单测（E4a）：状态机守卫 + hash 幂等 + 订阅收口。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import pytest

from autoanime.core.enums import (
    Decision,
    EpisodeState,
    MediaType,
    ReleaseStatus,
)
from autoanime.core.models import Episode, ReleaseRecord, RssSource, Season, Series
from autoanime.memory.store import SqliteStorage
from autoanime.scheduler.store import LoopStore, TransitionError


@pytest.fixture
async def store() -> AsyncIterator[LoopStore]:
    storage = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await storage.create_all()
    yield LoopStore(storage)
    await storage.close()


@pytest.fixture
async def season_id(store: LoopStore) -> int:
    series = await store.create_subscription(
        Series(title_cn="孤独摇滚", media_type=MediaType.TV, status="active"),
        Season(number=1),
        [Episode(number=n, state=EpisodeState.MISSING) for n in range(1, 6)],
    )
    seasons = await store.seasons_for_series(series.id)
    return seasons[0].id


async def test_create_subscription_pregenerates(store: LoopStore, season_id: int) -> None:
    episodes = await store.episodes_for_season(season_id)
    assert [row.number for row in episodes] == [1, 2, 3, 4, 5]
    assert all(row.state == EpisodeState.MISSING for row in episodes)


async def test_create_release_hash_unique_dedupes(store: LoopStore, season_id: int) -> None:
    episode = (await store.episodes_for_season(season_id))[0]
    record = ReleaseRecord(
        episode_id=episode.id, torrent_hash="a" * 40, source_url="u1"
    )
    first = await store.create_release(record)
    second = await store.create_release(
        ReleaseRecord(episode_id=episode.id, torrent_hash="a" * 40, source_url="u2")
    )
    assert first is not None
    assert second is None  # 撞哈希 = seen 去重


async def test_release_transition_guards(store: LoopStore, season_id: int) -> None:
    episode = (await store.episodes_for_season(season_id))[0]
    record = await store.create_release(
        ReleaseRecord(episode_id=episode.id, torrent_hash="b" * 40)
    )
    assert record is not None
    now = datetime(2026, 9, 6, 12, 0, 0)
    picked = await store.transition_release(
        record.id, ReleaseStatus.PICKED, now=now, decision=Decision.ACCEPTED
    )
    assert picked is not None
    assert picked.picked_at == now
    assert await store.transition_release(picked.id, ReleaseStatus.DOWNLOADING, now=now)
    # 非法转移：completed 不能回 candidate（直接拒绝）
    with pytest.raises(TransitionError):
        await store.transition_release(picked.id, ReleaseStatus.CANDIDATE, now=now)
    assert await store.transition_release(picked.id, ReleaseStatus.COMPLETED, now=now)
    row = await store.list_releases_by_status([ReleaseStatus.COMPLETED])
    assert row[0].finished_at == now


async def test_episode_transition_guards(store: LoopStore, season_id: int) -> None:
    episode = (await store.episodes_for_season(season_id))[0]
    assert await store.transition_episode(episode.id, EpisodeState.DOWNLOADING)
    with pytest.raises(TransitionError):
        await store.transition_episode(episode.id, EpisodeState.ORGANIZED)
    # D14 错配恢复回退路径：DOWNLOADING → MISSING 现已合法
    assert await store.transition_episode(episode.id, EpisodeState.MISSING)
    assert await store.transition_episode(episode.id, EpisodeState.DOWNLOADING)
    assert await store.transition_episode(episode.id, EpisodeState.DOWNLOADED)
    with pytest.raises(TransitionError):
        await store.transition_episode(episode.id, EpisodeState.DOWNLOADING)
    # B5：ORGANIZED → FLAGGED（对账），FLAGGED → ORGANIZED（恢复）
    assert await store.transition_episode(episode.id, EpisodeState.ORGANIZED)
    assert await store.transition_episode(episode.id, EpisodeState.FLAGGED)
    assert await store.transition_episode(episode.id, EpisodeState.ORGANIZED)


async def test_rss_source_roundtrip(store: LoopStore, season_id: int) -> None:
    saved = await store.add_rss_source(
        RssSource(url="https://mikanani.me/RSS/MyBangumi?token=x", season_id=season_id)
    )
    enabled = await store.enabled_rss_sources()
    assert [row.id for row in enabled] == [saved.id]
    await store.mark_polled(saved.id, datetime(2026, 9, 6, 12, 0, 0))
    fetched = await store.get_rss_source(saved.id)
    assert fetched is not None
    assert fetched.last_polled_at == datetime(2026, 9, 6, 12, 0, 0)
