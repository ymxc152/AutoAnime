from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from autoanime.core.enums import MediaType
from autoanime.core.models import (
    Base,
    Episode,
    ParseMemory,
    ReleaseRecord,
    Season,
    Series,
)


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _add_series(session, media_type: MediaType = MediaType.TV) -> Series:
    series = Series(title_cn="Test", media_type=media_type)
    session.add(series)
    await session.flush()
    return series


async def _add_season(session, series: Series) -> Season:
    season = Season(series_id=series.id, number=1)
    session.add(season)
    await session.flush()
    return season


async def _add_episode(session, series: Series, season: Season | None = None) -> Episode:
    episode = Episode(series_id=series.id, season_id=season.id if season else None, number=1)
    session.add(episode)
    await session.flush()
    return episode


async def test_exactly_twelve_tables(session) -> None:
    result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    names = {row[0] for row in result}
    expected = {
        "series",
        "season",
        "episode",
        "release_record",
        "parse_memory",
        "alias",
        "bypass_list",
        "pending_queue",
        "audit_log",
        "parse_events",
        "llm_cache",
        "reference_cache",
    }
    assert names == expected


async def test_movie_episode_can_use_null_season(session) -> None:
    series = await _add_series(session, MediaType.MOVIE)
    episode = await _add_episode(session, series)
    assert series.media_type == MediaType.MOVIE
    assert episode.season_id is None


async def test_release_record_requires_exactly_one_target(session) -> None:
    series = await _add_series(session)
    season = await _add_season(session, series)
    episode = await _add_episode(session, series, season)

    season_target = ReleaseRecord(season_id=season.id, torrent_hash="season")
    episode_target = ReleaseRecord(episode_id=episode.id, torrent_hash="episode")
    session.add_all([season_target, episode_target])
    await session.flush()

    with pytest.raises(IntegrityError):
        session.add(ReleaseRecord(torrent_hash="none"))
        await session.flush()
    await session.rollback()

    with pytest.raises(IntegrityError):
        session.add(ReleaseRecord(season_id=season.id, episode_id=episode.id, torrent_hash="both"))
        await session.flush()
    await session.rollback()


async def test_parse_memory_key_is_unique(session) -> None:
    first = ParseMemory(key_level=1, key_hash="same", title_shape="shape", result={})
    second = ParseMemory(key_level=1, key_hash="same", title_shape="shape", result={})
    session.add_all([first, second])
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
