"""scheduler.rss_poller 单测（E4a）：全离线——fake 管线 + fake 网关 + mock HTTP。

场景覆盖：新条目择优下最高分、seen 去重、错标 conflict 拒绝、段不支持
拒绝、unparsed backlog、ORGANIZED 过洗版阈值、COLLECTED 降频、缺口上报、
拉取失败跳过本轮不 crash、expected 载体落库。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
import pytest

from autoanime.core.enums import (
    Confidence,
    EpisodeState,
    MediaType,
    ReleaseStatus,
    SeasonState,
    Segment,
)
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.core.models import Episode, RssSource, Season, Series
from autoanime.gateway.torrents import bencode, torrent_info_hash
from autoanime.memory.store import SqliteStorage
from autoanime.scheduler.rss_poller import RssPoller
from autoanime.scheduler.store import LoopStore

#: 本文件内创建的内存库（autouse fixture 统一关闭，防 aiosqlite 线程泄漏）。
_OPEN_STORAGES: list[SqliteStorage] = []


@pytest.fixture(autouse=True)
async def _close_storages() -> Any:
    yield
    for storage in _OPEN_STORAGES:
        await storage.close()
    _OPEN_STORAGES.clear()


class FakeRecognizer:
    """确定性识别器：标题精确匹配 → 预置 ParseResult；未命中 = None（backlog）。"""

    def __init__(self, mapping: dict[str, ParseResult]) -> None:
        self._mapping = mapping

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        return self._mapping.get(raw.name)


class FakeGateway:
    """网关 fake：记录提交；状态查询供 download_poller 测试另建。"""

    def __init__(self) -> None:
        self.added: list[bytes] = []

    async def add_torrent_bytes(self, data: bytes, *, save_path: str | None = None) -> str:
        self.added.append(data)
        return torrent_info_hash(data)

    async def status(self, torrent_hash: str) -> dict[str, object] | None:
        return None

    async def completed_hashes(self) -> list[str]:
        return []

    async def files(self, torrent_hash: str) -> list[dict[str, object]]:
        return []


def _parse_result(
    title: str, episode: int | None, *, segment: Segment = Segment.EPISODE
) -> ParseResult:
    return ParseResult(
        title=title,
        season=1,
        episode=episode,
        segment=segment,
        fansub="LoliHouse",
        level=Confidence.HIGH,
        confidence=0.99,
    )


def _torrent(filename: str) -> bytes:
    return bencode({"info": {"name": filename, "length": 5}})


def _feed(entries: list[tuple[str, str, str]]) -> bytes:
    """(guid, title, torrent 文件名) → RSS XML。"""
    items = [
        f"<item><guid>{guid}</guid><title>{title}</title>"
        f"<enclosure type='application/x-bittorrent' length='100' "
        f"url='https://mikanani.me/Download/{filename}'/></item>"
        for guid, title, filename in entries
    ]
    return ("<rss><channel><title>feed</title>" + "".join(items) + "</channel></rss>").encode()


NOW = datetime(2026, 9, 6, 12, 0, 0)


#: 合法状态机路径：make_rig 的 overrides 沿链转移（非法转移被守卫拒绝）。
_STATE_CHAINS: dict[EpisodeState, tuple[EpisodeState, ...]] = {
    EpisodeState.DOWNLOADING: (EpisodeState.DOWNLOADING,),
    EpisodeState.DOWNLOADED: (EpisodeState.DOWNLOADING, EpisodeState.DOWNLOADED),
    EpisodeState.ORGANIZED: (
        EpisodeState.DOWNLOADING,
        EpisodeState.DOWNLOADED,
        EpisodeState.ORGANIZED,
    ),
    EpisodeState.UPGRADED: (
        EpisodeState.DOWNLOADING,
        EpisodeState.DOWNLOADED,
        EpisodeState.ORGANIZED,
        EpisodeState.UPGRADED,
    ),
}


async def _force_state(store: LoopStore, season_id: int, number: int, target: EpisodeState) -> None:
    row = await store.episode_for_number(season_id, number)
    assert row is not None
    for state in _STATE_CHAINS[target]:
        await store.transition_episode(row.id, state)


async def _no_sleep(_seconds: float) -> None:
    return None


class Rig:
    """测试装配：内存库 + 订阅好的番 + fake 组件。"""

    def __init__(
        self,
        store: LoopStore,
        storage: SqliteStorage,
        gateway: FakeGateway,
        poller: RssPoller,
        season_id: int,
        source_id: int,
    ) -> None:
        self.store = store
        self.storage = storage
        self.gateway = gateway
        self.poller = poller
        self.season_id = season_id
        self.source_id = source_id

    async def source(self) -> RssSource:
        row = await self.store.get_rss_source(self.source_id)
        assert row is not None
        return row

    async def episode(self, number: int) -> Episode:
        rows = await self.store.episodes_for_season(self.season_id)
        return next(row for row in rows if row.number == number)


async def make_rig(
    mapping: dict[str, ParseResult],
    feed_entries: list[tuple[str, str, str]],
    *,
    season_status: SeasonState = SeasonState.AIRING,
    episode_overrides: dict[int, EpisodeState] | None = None,
    episode_scores: dict[int, float] | None = None,
    last_polled_at: datetime | None = None,
    client_factory: Any | None = None,
    fetch_retries: int = 2,
) -> Rig:
    storage = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await storage.create_all()
    _OPEN_STORAGES.append(storage)
    store = LoopStore(storage)
    series = await store.create_subscription(
        Series(title_cn="孤独摇滚", media_type=MediaType.TV, status="active"),
        Season(number=1, status=season_status),
        [Episode(number=n, state=EpisodeState.MISSING) for n in range(1, 6)],
    )
    season = (await store.seasons_for_series(series.id))[0]
    for number, state in (episode_overrides or {}).items():
        await _force_state(store, season.id, number, state)
    for number, score in (episode_scores or {}).items():
        row = await store.episode_for_number(season.id, number)
        assert row is not None
        row.quality_score = score
        await storage.add(row)
    source = await store.add_rss_source(
        RssSource(
            url="https://mikanani.me/RSS/MyBangumi?token=secret",
            season_id=season.id,
            last_polled_at=last_polled_at,
        )
    )
    gateway = FakeGateway()
    if client_factory is None:
        def _default_factory() -> httpx.AsyncClient:
            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.startswith("/RSS"):
                    return httpx.Response(200, content=_feed(feed_entries))
                return httpx.Response(
                    200, content=_torrent(request.url.path.rsplit("/", 1)[-1])
                )

            return httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client_factory = _default_factory

    poller = RssPoller(
        store,
        FakeRecognizer(mapping),
        gateway,
        client_factory=client_factory,
        sleeper=_no_sleep,
        fetch_retries=fetch_retries,
    )
    return Rig(store, storage, gateway, poller, season.id, source.id)


HIGH_1080 = "[LoliHouse] Bocchi - 01 [Baha 1080p HEVC]"
LOW_720 = "[SubGroup] Bocchi - 01 [720p HDTV]"
EP05 = "[LoliHouse] Bocchi - 05 [1080p]"


async def test_poll_picks_best_candidate_and_marks_downloading() -> None:
    mapping = {
        HIGH_1080: _parse_result("孤独摇滚", 1),
        LOW_720: _parse_result("孤独摇滚", 1),
        EP05: _parse_result("孤独摇滚", 5),
    }
    rig = await make_rig(
        mapping,
        [
            ("guid-a", HIGH_1080, "a.torrent"),
            ("guid-b", LOW_720, "b.torrent"),
            ("guid-c", EP05, "c.torrent"),
        ],
    )
    report = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert report.picked == 2  # E01 只下最高分（1080p），E05 单候选
    assert len(rig.gateway.added) == 2
    added_hashes = {torrent_info_hash(data) for data in rig.gateway.added}
    assert torrent_info_hash(_torrent("a.torrent")) in added_hashes  # 高分者胜
    assert torrent_info_hash(_torrent("b.torrent")) not in added_hashes
    e1 = await rig.episode(1)
    e5 = await rig.episode(5)
    assert e1.state == EpisodeState.DOWNLOADING
    assert e5.state == EpisodeState.DOWNLOADING
    picked = await rig.store.list_releases_by_status([ReleaseStatus.PICKED])
    assert len(picked) == 2
    assert all(release.decision.value == "accepted" for release in picked)
    assert all(release.episode_id is not None for release in picked)


async def test_poll_seen_conflict_backlog_routing() -> None:
    other_show = "[Sub] Frieren - 03 [1080p]"
    mapping = {
        HIGH_1080: _parse_result("孤独摇滚", 1),
        other_show: _parse_result("葬送的芙莉莲", 3),
    }
    rig = await make_rig(
        mapping,
        [
            ("guid-a", HIGH_1080, "a.torrent"),
            ("guid-dup", HIGH_1080, "a.torrent"),  # 同种子：seen
            ("guid-x", other_show, "x.torrent"),  # 错标：conflict
            ("guid-y", "[Sub] totally unknown release", "y.torrent"),  # backlog
        ],
    )
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.picked == 1
    assert outcome.seen == 1
    assert outcome.rejected == 1
    assert outcome.backlog == 1
    candidates = await rig.store.list_releases_by_status([ReleaseStatus.CANDIDATE])
    assert any(
        release.reason is not None and release.reason.startswith("expected_conflict")
        for release in candidates
    )


async def test_poll_season_pack_rejected() -> None:
    title = "[LoliHouse] Bocchi Season 1 [Baha]"
    rig = await make_rig(
        {title: _parse_result("孤独摇滚", None, segment=Segment.SEASON_PACK)},
        [("guid-a", title, "a.torrent")],
    )
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.rejected == 1
    assert outcome.picked == 0
    assert rig.gateway.added == []


async def test_poll_organized_requires_upgrade_threshold() -> None:
    high_title = "[LoliHouse] Bocchi - 03 [Baha 1080p HEVC]"
    low_title = "[SubGroup] Bocchi - 02 [720p HDTV]"
    mapping = {
        high_title: _parse_result("孤独摇滚", 3),
        low_title: _parse_result("孤独摇滚", 2),
    }
    rig = await make_rig(
        mapping,
        [("guid-b", low_title, "b.torrent"), ("guid-c", high_title, "c.torrent")],
        episode_overrides={2: EpisodeState.ORGANIZED, 3: EpisodeState.ORGANIZED},
        episode_scores={2: 7.0, 3: 4.0},
    )
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.picked == 1  # E03：4.5+ 分候选 ≥ 4+2？否 → 详见断言
    pending = await rig.store.list_releases_by_status([ReleaseStatus.CANDIDATE])
    reasons = {release.reason for release in pending if release.reason}
    # E02（现分 7）：720p/HDTV/无字幕组偏好 ≈ 2+1+1 = 4 < 7+2 → 不触发
    assert "upgrade: threshold_not_met" in reasons
    # E03（现分 4）：1080p/Baha/HEVC/命中偏好 ≈ 4+3+2+2 = 11 ≥ 4+2 → 触发
    assert outcome.picked == 1


async def test_poll_downloading_episode_not_repicked() -> None:
    title = "[LoliHouse] Bocchi - 02 [1080p]"
    mapping = {title: _parse_result("孤独摇滚", 2)}
    rig = await make_rig(
        mapping,
        [("guid-a", title, "a.torrent"), ("guid-b", title, "b.torrent")],
        episode_overrides={},
    )
    # 先让 E02 进入 DOWNLOADING
    e2 = await rig.episode(2)
    await rig.store.transition_episode(e2.id, EpisodeState.DOWNLOADING)
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.picked == 0
    pending = await rig.store.list_releases_by_status([ReleaseStatus.CANDIDATE])
    assert any(release.reason == "already_downloading" for release in pending)


async def test_poll_skips_when_not_due_collected() -> None:
    title = "[LoliHouse] Bocchi - 01 [1080p]"
    mapping = {title: _parse_result("孤独摇滚", 1)}
    rig = await make_rig(
        mapping,
        [("guid-a", title, "a.torrent")],
        season_status=SeasonState.COLLECTED,
        last_polled_at=NOW,
    )
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.skipped_not_due is True
    assert rig.gateway.added == []


async def test_poll_reports_gap() -> None:
    title = "[LoliHouse] Bocchi - 01 [1080p]"
    mapping = {title: _parse_result("孤独摇滚", 1)}
    rig = await make_rig(mapping, [])
    for number in range(1, 6):
        row = await rig.episode(number)
        row.air_date = date(2026, 8, 1)
        await rig.storage.add(row)
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.gaps == (1, 2, 3, 4, 5)  # D15：缺口报告（回补等 RSS 自然命中）


async def test_poll_fetch_failure_skips_round_without_crash() -> None:
    title = "[LoliHouse] Bocchi - 01 [1080p]"
    mapping = {title: _parse_result("孤独摇滚", 1)}

    def dead_client() -> httpx.AsyncClient:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    rig = await make_rig(mapping, [], client_factory=dead_client, fetch_retries=1)
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.fetch_error == "unreachable after retries"
    assert outcome.picked == 0
    refreshed = await rig.source()
    assert refreshed.last_polled_at is not None  # mark_polled 仍更新


async def test_poll_writes_expected_carrier_release_record() -> None:
    """D13：release_record(episode_id, torrent_hash) 在候选提交时即落库。"""
    title = "[LoliHouse] Bocchi - 04 [1080p]"
    mapping = {title: _parse_result("孤独摇滚", 4)}
    rig = await make_rig(mapping, [("guid-a", title, "a.torrent")])
    await rig.poller.poll_source(await rig.source(), now=NOW)
    picked = await rig.store.list_releases_by_status([ReleaseStatus.PICKED])
    assert len(picked) == 1
    record = picked[0]
    assert len(record.torrent_hash) == 40
    assert record.picked_at is not None
    assert record.torrent_hash == torrent_info_hash(rig.gateway.added[0])
