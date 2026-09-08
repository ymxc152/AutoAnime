"""库收纳单测（P0-B）：import 归档文件的 series/season/episode upsert。

覆盖：归一化标题查重合并（shape 相同同番）、media_type 参与比对（剧场版
vs TV 同题不合并）、集号语义（movie → s1/e1、季包解不出集号 → number=0）、
幂等（同指针 no-op / 新文件更新指针 / 盘上无文件不覆盖）、异常吞掉不抛出。
订阅 adopt 消幻影（queries 层）：先 import 后订阅 → 保留 ORGANIZED 只补
缺失集号 MISSING；bangumi_id 精确收编；RSS 同事务挂载。

全离线：tmp SQLite + 真实 store 层，不触网。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from autoanime.core.enums import EpisodeState, MediaType
from autoanime.core.models import Episode, Season, Series
from autoanime.memory.alias import AliasService
from autoanime.memory.store import SqliteStorage
from autoanime.organize.library_ingest import IngestReport, upsert_archived_file
from autoanime.scheduler.store import LoopStore
from autoanime.web.queries import ApiStore


def _make_db(path: Path) -> SqliteStorage:
    return SqliteStorage(f"sqlite+aiosqlite:///{path.as_posix()}")


def _rows(db_path: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql).fetchall()


async def _upsert(store: LoopStore, **overrides):
    kwargs: dict[str, object] = {
        "titles": {"title_cn": "Bocchi the Rock"},
        "media_type": "tv",
        "season_number": 1,
        "episode_number": 1,
        "file_path": "/library/Bocchi the Rock/Season 01/ep01.mkv",
    }
    kwargs.update(overrides)
    return await upsert_archived_file(store, **kwargs)  # type: ignore[arg-type]


def _make_series(**overrides: object) -> Series:
    fields: dict[str, object] = {
        "title_cn": "葬送的芙莉莲",
        "media_type": MediaType.TV,
        "status": "active",
    }
    fields.update(overrides)
    return Series(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 基础建树


def test_upsert_creates_series_tree(tmp_path: Path) -> None:
    db_path = tmp_path / "ingest.db"

    async def scenario() -> IngestReport:
        db = _make_db(db_path)
        await db.create_all()
        try:
            return await _upsert(LoopStore(db))
        finally:
            await db.close()

    report = asyncio.run(scenario())
    assert report.ok
    assert report.created_series and report.created_season and report.created_episode
    assert _rows(db_path, "SELECT title_cn, media_type, status FROM series") == [
        ("Bocchi the Rock", "tv", "active")
    ]
    assert _rows(db_path, "SELECT number FROM season") == [(1,)]
    assert _rows(
        db_path, "SELECT number, state, file_path FROM episode"
    ) == [(1, "organized", "/library/Bocchi the Rock/Season 01/ep01.mkv")]


def test_title_shape_merges_into_same_series(tmp_path: Path) -> None:
    """归一化查重：分隔符/大小写不同的同题名合并进同一 Series（新集号新行）。"""
    db_path = tmp_path / "ingest.db"

    async def scenario() -> tuple[IngestReport, IngestReport]:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            first = await _upsert(store)
            second = await _upsert(
                store,
                titles={"title_cn": "BOCCHI_THE_ROCK"},
                episode_number=2,
                file_path="/library/x/ep02.mkv",
            )
            return first, second
        finally:
            await db.close()

    first, second = asyncio.run(scenario())
    assert first.ok and second.ok
    assert second.series_id == first.series_id
    assert not second.created_series and not second.created_season
    assert second.created_episode
    assert len(_rows(db_path, "SELECT id FROM series")) == 1
    assert [row[0] for row in _rows(db_path, "SELECT number FROM episode")] == [1, 2]


def test_movie_and_tv_same_title_not_merged(tmp_path: Path) -> None:
    """media_type 参与比对：剧场版与 TV 同题不合并（计划 §风险 3）。"""
    db_path = tmp_path / "ingest.db"

    async def scenario() -> tuple[IngestReport, IngestReport]:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            tv = await _upsert(store)
            movie = await _upsert(
                store,
                titles={"title_cn": "Bocchi the Rock"},
                media_type="movie",
                season_number=None,
                episode_number=None,
                file_path="/library/movie.mkv",
            )
            return tv, movie
        finally:
            await db.close()

    tv, movie = asyncio.run(scenario())
    assert movie.ok and movie.created_series
    assert movie.series_id != tv.series_id
    # 剧场版集号语义：episode_number=None → season 1 / number 1
    assert _rows(
        db_path, f"SELECT number FROM season WHERE series_id = {movie.series_id}"
    ) == [(1,)]
    assert _rows(
        db_path,
        f"SELECT number, state FROM episode WHERE series_id = {movie.series_id}",
    ) == [(1, "organized")]


def test_season_pack_without_episode_number_maps_to_zero(tmp_path: Path) -> None:
    """季包解不出集号 → number=0（tradeoff：该季 complete 永假，仅影响降频）。"""
    db_path = tmp_path / "ingest.db"

    async def scenario() -> IngestReport:
        db = _make_db(db_path)
        await db.create_all()
        try:
            return await _upsert(LoopStore(db), episode_number=None)
        finally:
            await db.close()

    report = asyncio.run(scenario())
    assert report.ok and report.created_episode
    assert _rows(db_path, "SELECT number, state FROM episode") == [(0, "organized")]


# ---------------------------------------------------------------- 幂等


def test_idempotent_same_pointer_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "ingest.db"

    async def scenario() -> tuple[IngestReport, IngestReport]:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            first = await _upsert(store)
            second = await _upsert(store)
            return first, second
        finally:
            await db.close()

    first, second = asyncio.run(scenario())
    assert second.ok
    assert second.episode_id == first.episode_id
    assert not second.created_episode and not second.updated_file
    assert len(_rows(db_path, "SELECT id FROM episode")) == 1


def test_idempotent_new_file_updates_pointer(tmp_path: Path) -> None:
    """同 series+season+number 已有 ORGANIZED 行：新文件在盘上 → 更新 file_path。"""
    db_path = tmp_path / "ingest.db"
    new_file = tmp_path / "better.mkv"
    new_file.write_bytes(b"better")

    async def scenario() -> IngestReport:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            await _upsert(store)
            return await _upsert(store, file_path=str(new_file))
        finally:
            await db.close()

    report = asyncio.run(scenario())
    assert report.ok and report.updated_file
    assert _rows(db_path, "SELECT file_path FROM episode") == [(str(new_file),)]


def test_idempotent_keeps_pointer_when_file_missing(tmp_path: Path) -> None:
    """同集号不同 file_path 但新文件不在盘上 → no-op 保留现指针。"""
    db_path = tmp_path / "ingest.db"
    ghost = "/nowhere/ghost.mkv"

    async def scenario() -> IngestReport:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            await _upsert(store)
            return await _upsert(store, file_path=ghost)
        finally:
            await db.close()

    report = asyncio.run(scenario())
    assert report.ok and not report.updated_file
    assert report.note is not None and "missing" in report.note
    assert _rows(db_path, "SELECT file_path FROM episode") != [(ghost,)]


def test_missing_row_transitions_to_organized(tmp_path: Path) -> None:
    """订阅预生成的 MISSING 行被 import 归档命中 → 合法转移 ORGANIZED（非重插）。"""
    db_path = tmp_path / "ingest.db"
    archived = tmp_path / "ep01.mkv"
    archived.write_bytes(b"v")

    async def scenario() -> IngestReport:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            series = Series(title_cn="Bocchi the Rock", media_type=MediaType.TV)
            season = Season(number=1)
            await store.create_subscription(series, season, [Episode(number=1)])
            return await _upsert(store, file_path=str(archived))
        finally:
            await db.close()

    report = asyncio.run(scenario())
    assert report.ok and not report.created_episode
    rows = _rows(db_path, "SELECT id, state, file_path FROM episode")
    assert len(rows) == 1 and rows[0][0] == report.episode_id
    assert rows[0][1] == "organized"


def test_flagged_row_adopted_back_to_organized(tmp_path: Path) -> None:
    """FLAGGED 行（对账标缺）在文件重新放回时被收编 → 合法转移 ORGANIZED。

    FLAGGED→ORGANIZED 本就是状态机合法转移；白名单收编 FLAGGED 后
    import/confirm 重跑可把恢复的文件重新归位，而非「untouched」卡死。
    """
    db_path = tmp_path / "ingest.db"
    restored = tmp_path / "ep01.mkv"
    restored.write_bytes(b"restored")

    async def scenario() -> IngestReport:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            await store.create_subscription(
                Series(title_cn="Bocchi the Rock", media_type=MediaType.TV),
                Season(number=1),
                [Episode(number=1, state=EpisodeState.FLAGGED)],
            )
            return await _upsert(store, file_path=str(restored))
        finally:
            await db.close()

    report = asyncio.run(scenario())
    assert report.ok and report.updated_file
    assert _rows(db_path, "SELECT state, file_path FROM episode") == [
        ("organized", str(restored))
    ]


# ------------------------------------------------------- 别名兜底（标题裂库缓解）


def test_alias_title_merges_into_subscribed_series(tmp_path: Path) -> None:
    """解析标题 = 订阅番的别名（Alias 表命中）→ 收编同一 Series，不新建。"""
    db_path = tmp_path / "ingest.db"

    async def scenario() -> IngestReport:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            series = await store.create_subscription(
                Series(title_cn="葬送的芙莉莲", media_type=MediaType.TV),
                Season(number=1),
                [],
            )
            # 用户确认/参考源回填登记的别名：解析结论标题命中别名 shape
            await AliasService(db).add_alias(series.id, "Frieren")
            return await _upsert(store, titles={"title_cn": "Frieren"})
        finally:
            await db.close()

    report = asyncio.run(scenario())
    assert report.ok and not report.created_series
    assert report.created_episode
    assert len(_rows(db_path, "SELECT id FROM series")) == 1


# ---------------------------------------------------------------- 异常吞掉


def test_exception_is_swallowed_into_failed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """任何异常吞掉记 warning 返回失败 report，绝不抛出拖垮导入批。"""

    async def broken(*args: object, **kwargs: object) -> list[Series]:
        raise RuntimeError("db on fire")

    async def scenario() -> IngestReport:
        db = _make_db(tmp_path / "ingest.db")
        await db.create_all()
        try:
            store = LoopStore(db)
            monkeypatch.setattr(store, "list_series", broken)
            return await _upsert(store)
        finally:
            await db.close()

    report = asyncio.run(scenario())
    assert not report.ok
    assert report.note == "ingest-error"


# ------------------------------------------------- 订阅 adopt 消幻影（queries）


def test_adopt_preserves_organized_and_fills_missing(tmp_path: Path) -> None:
    """先 import 后订阅：ORGANIZED 行保留、只补缺失集号，无整季幻影。"""
    db_path = tmp_path / "ingest.db"

    async def scenario() -> tuple[bool, list[tuple[int, str]]]:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            for number in (1, 2):
                assert (
                    await _upsert(
                        store, episode_number=number, file_path=f"/lib/ep{number}.mkv"
                    )
                ).ok
            api = ApiStore(db)
            result = await api.create_or_adopt_subscription(
                _make_series(title_cn="Bocchi the Rock"),
                Season(number=1),
                [
                    Episode(number=number, state=EpisodeState.MISSING)
                    for number in range(1, 5)
                ],
            )
            season = (await store.seasons_for_series(result.series.id))[0]
            rows = await store.episodes_for_season(season.id)
            return result.adopted, sorted(
                (row.number, str(row.state.value)) for row in rows
            )
        finally:
            await db.close()

    adopted, rows = asyncio.run(scenario())
    assert adopted
    assert rows == [
        (1, "organized"),
        (2, "organized"),
        (3, "missing"),
        (4, "missing"),
    ]
    assert len(_rows(db_path, "SELECT id FROM series")) == 1
    assert _rows(db_path, "SELECT count(*) FROM episode")[0][0] == 4


def test_adopt_by_bangumi_id_and_backfills_empty_titles(tmp_path: Path) -> None:
    """bangumi_id 精确收编优先于标题；空标题槽回填，已有标题不覆盖。"""
    db_path = tmp_path / "ingest.db"

    async def scenario() -> tuple[bool, tuple[str | None, str | None, str]]:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            assert (
                await _upsert(
                    store,
                    titles={"title_cn": "Frieren"},
                    bangumi_id="999",
                    file_path="/lib/ep1.mkv",
                )
            ).ok
            api = ApiStore(db)
            result = await api.create_or_adopt_subscription(
                _make_series(
                    title_cn=None, title_jp="葬送のフリーレン", bangumi_id="999"
                ),
                Season(number=1),
                [],
            )
            rows = await store.list_series()
            return result.adopted, (rows[0].title_cn, rows[0].title_jp, rows[0].status)
        finally:
            await db.close()

    adopted, titles = asyncio.run(scenario())
    assert adopted
    # 已有 title_cn 不覆盖；空 title_jp 回填；status 置回 active
    assert titles == ("Frieren", "葬送のフリーレン", "active")
    assert len(_rows(db_path, "SELECT id FROM series")) == 1


def test_adopt_creates_missing_season_and_rss_in_same_transaction(
    tmp_path: Path,
) -> None:
    """命中已有 Series 但季不存在 → find_or_create Season；rss_url 同事务挂载。"""
    db_path = tmp_path / "ingest.db"

    async def scenario() -> tuple[bool, bool, bool]:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            assert (await _upsert(store, file_path="/lib/ep1.mkv")).ok
            api = ApiStore(db)
            first = await api.create_or_adopt_subscription(
                _make_series(title_cn="Bocchi the Rock"),
                Season(number=2),
                [Episode(number=1, state=EpisodeState.MISSING)],
                rss_url="https://mikan.example/RSS/MyBangumi",
                rss_token="tok",
            )
            second = await api.create_or_adopt_subscription(
                _make_series(title_cn="Bocchi the Rock"),
                Season(number=2),
                [],
                rss_url="https://mikan.example/RSS/MyBangumi",
            )
            no_rss = await api.create_or_adopt_subscription(
                _make_series(title_cn="Bocchi the Rock"), Season(number=3), []
            )
            sources = _rows(db_path, "SELECT season_id, url, token FROM rss_sources")
            assert len(sources) == 1 and sources[0][2] == "tok"
            return (
                first.adopted and first.rss_saved,
                second.adopted and not second.rss_saved,  # 同季同 URL 不重复建
                no_rss.adopted and not no_rss.rss_saved,
            )
        finally:
            await db.close()

    adopt_rss, dup_rss, bare = asyncio.run(scenario())
    assert adopt_rss and dup_rss and bare
    assert len(_rows(db_path, "SELECT id FROM season")) == 3  # 原季1 + adopt 新建 2/3 季


def test_no_match_falls_back_to_plain_create(tmp_path: Path) -> None:
    """未命中 → 与原 create_subscription 同语义建树（向后兼容）。"""
    db_path = tmp_path / "ingest.db"

    async def scenario() -> bool:
        db = _make_db(db_path)
        await db.create_all()
        try:
            store = LoopStore(db)
            await _upsert(store, titles={"title_cn": "Other Show"},
                          file_path="/lib/ep1.mkv")
            result = await ApiStore(db).create_or_adopt_subscription(
                _make_series(), Season(number=1),
                [Episode(number=1, state=EpisodeState.MISSING)],
            )
            return (not result.adopted) and result.series.id is not None
        finally:
            await db.close()

    assert asyncio.run(scenario())
    assert len(_rows(db_path, "SELECT id FROM series")) == 2
