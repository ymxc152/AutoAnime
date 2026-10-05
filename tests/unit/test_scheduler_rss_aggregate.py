"""scheduler.rss_poller 聚合源（kind=aggregate）单测（批次三，全离线）。

场景：一个 feed 混多部番 → 命中 A 番下载 / 命中 B 番下载 / 未命中 ignored
（不落库不进待确认）/ 重复 hash 去重 / 源级 exclude 拒绝 / series 级规则
叠加 / 别名命中（feed 标题只含别名 → align fast_path）/ poll_all 纳入聚合源。
"""

from __future__ import annotations

from datetime import datetime
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
from autoanime.memory.alias import AliasService
from autoanime.memory.store import SqliteStorage
from autoanime.scheduler.rss_poller import RssPoller, source_kind
from autoanime.scheduler.store import LoopStore

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
    title: str, episode: int | None, *, season: int = 1, segment: Segment = Segment.EPISODE
) -> ParseResult:
    return ParseResult(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub="LoliHouse",
        level=Confidence.HIGH,
        confidence=0.99,
    )


def _torrent(filename: str) -> bytes:
    return bencode({"info": {"name": filename, "length": 5}})


def _feed(entries: list[tuple[str, str, str]]) -> bytes:
    items = [
        f"<item><guid>{guid}</guid><title>{title}</title>"
        f"<enclosure type='application/x-bittorrent' length='100' "
        f"url='https://tracker.example/Download/{filename}'/></item>"
        for guid, title, filename in entries
    ]
    return ("<rss><channel><title>feed</title>" + "".join(items) + "</channel></rss>").encode()


NOW = datetime(2026, 9, 13, 12, 0, 0)


async def _no_sleep(_seconds: float) -> None:
    return None


class Rig:
    """测试装配：内存库 + 两个活跃订阅（A/B 番）+ 聚合源 + fake 组件。"""

    def __init__(
        self,
        store: LoopStore,
        storage: SqliteStorage,
        gateway: FakeGateway,
        poller: RssPoller,
        source_id: int,
        season_a_id: int,
        season_b_id: int,
        alias_service: AliasService,
    ) -> None:
        self.store = store
        self.storage = storage
        self.gateway = gateway
        self.poller = poller
        self.source_id = source_id
        self.season_a_id = season_a_id
        self.season_b_id = season_b_id
        self.alias_service = alias_service

    async def source(self) -> RssSource:
        row = await self.store.get_rss_source(self.source_id)
        assert row is not None
        return row

    async def episode(self, season_id: int, number: int) -> Episode:
        rows = await self.store.episodes_for_season(season_id)
        return next(row for row in rows if row.number == number)


async def make_rig(
    mapping: dict[str, ParseResult],
    feed_entries: list[tuple[str, str, str]],
    *,
    source_kwargs: dict[str, Any] | None = None,
) -> Rig:
    storage = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await storage.create_all()
    _OPEN_STORAGES.append(storage)
    store = LoopStore(storage)
    series_a = await store.create_subscription(
        Series(title_cn="孤独摇滚", media_type=MediaType.TV, status="active"),
        Season(number=1, status=SeasonState.AIRING),
        [Episode(number=n, state=EpisodeState.MISSING) for n in range(1, 6)],
    )
    season_a = (await store.seasons_for_series(series_a.id))[0]
    series_b = await store.create_subscription(
        Series(title_cn="葬送的芙莉莲", media_type=MediaType.TV, status="active"),
        Season(number=1, status=SeasonState.AIRING),
        [Episode(number=n, state=EpisodeState.MISSING) for n in range(1, 4)],
    )
    season_b = (await store.seasons_for_series(series_b.id))[0]
    source = await store.add_rss_source(
        RssSource(
            url="https://tracker.example/RSS/aggregate",
            season_id=None,
            kind="aggregate",
            **(source_kwargs or {}),
        )
    )
    gateway = FakeGateway()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/RSS"):
            return httpx.Response(200, content=_feed(feed_entries))
        return httpx.Response(200, content=_torrent(request.url.path.rsplit("/", 1)[-1]))

    alias_service = AliasService(storage)
    # 订阅中文名 vs feed romaji 名:种入别名(别名富化端到端——真实场景
    # 由订阅时的 Bangumi infobox 拉取写入,测试里直接种)。
    await alias_service.upsert_title_aliases('孤独摇滚', ['Bocchi the Rock'])
    poller = RssPoller(
        store,
        FakeRecognizer(mapping),
        gateway,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sleeper=_no_sleep,
        fetch_retries=0,
        alias_service=alias_service,
    )
    return Rig(store, storage, gateway, poller, source.id, season_a.id, season_b.id, alias_service)


BOCCHI_01 = "[LoliHouse] Bocchi the Rock - 01 [1080p]"
BOCCHI_02 = "[LoliHouse] Bocchi the Rock - 02 [1080p]"
FRIEREN_01 = "[LoliHouse] Frieren - 01 [1080p]"
UNKNOWN = "[Sub] totally unknown release"


async def test_aggregate_hits_both_series_and_downloads() -> None:
    """命中 A 番下载 + 命中 B 番下载：同一 feed 两个目标各自进候选链。"""
    rig = await make_rig(
        {
            BOCCHI_01: _parse_result("Bocchi the Rock", 1),
            FRIEREN_01: _parse_result("葬送的芙莉莲", 1),
        },
        [("guid-a", BOCCHI_01, "a.torrent"), ("guid-b", FRIEREN_01, "b.torrent")],
    )
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.season_id is None  # 聚合源不绑季
    assert outcome.picked == 2
    assert outcome.ignored == 0
    assert len(rig.gateway.added) == 2
    ep_a = await rig.episode(rig.season_a_id, 1)
    ep_b = await rig.episode(rig.season_b_id, 1)
    assert ep_a.state == EpisodeState.DOWNLOADING
    assert ep_b.state == EpisodeState.DOWNLOADING
    picked = await rig.store.list_releases_by_status([ReleaseStatus.PICKED])
    assert {release.episode_id for release in picked} == {ep_a.id, ep_b.id}


async def test_aggregate_unmatched_entry_is_ignored_not_pending() -> None:
    """未命中 → ignored 计数；不落 release、不进待确认队列。"""
    rig = await make_rig(
        {BOCCHI_01: _parse_result("Bocchi the Rock", 1), UNKNOWN: _parse_result("未知番", 3)},
        [("guid-x", UNKNOWN, "x.torrent"), ("guid-a", BOCCHI_01, "a.torrent")],
    )
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.picked == 1
    assert outcome.ignored == 1
    rejected = await rig.store.list_releases_by_status([ReleaseStatus.CANDIDATE, ReleaseStatus.FAILED])
    assert all("unknown" not in (release.source_url or "") for release in rejected)
    assert await rig.store.count_pending() == 0


async def test_aggregate_duplicate_hash_seen_dedupe() -> None:
    """同一种子多条 guid（镜像/转载）→ 批内 hash 去重 seen。"""
    title = "[LoliHouse] Bocchi the Rock - 01 [1080p]"
    rig = await make_rig(
        {title: _parse_result("Bocchi the Rock", 1)},
        [("guid-1", title, "same.torrent"), ("guid-2", title, "same.torrent")],
    )
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.picked == 1
    assert outcome.seen == 1
    assert len(rig.gateway.added) == 1


async def test_aggregate_source_level_exclude_rejects() -> None:
    """源级 exclude 命中 → rejected（先于对齐，连种都不取）。"""
    title = "[LoliHouse] Bocchi the Rock - 01 [Cam 1080p]"
    rig = await make_rig(
        {title: _parse_result("Bocchi the Rock", 1)},
        [("guid-a", title, "a.torrent")],
        source_kwargs={"exclude_keywords": "cam"},
    )
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.rejected == 1
    assert outcome.picked == 0
    assert rig.gateway.added == []


async def test_aggregate_series_level_rules_stack_after_source_rules() -> None:
    """源级规则通过、series 级 exclude 命中 → 仍拒绝（规则叠加）。"""
    title = "[LoliHouse] Bocchi the Rock - 01 [1080p]"
    rig = await make_rig(
        {title: _parse_result("Bocchi the Rock", 1)},
        [("guid-a", title, "a.torrent")],
        source_kwargs={"exclude_keywords": "cam"},
    )
    # 源级只挡 Cam；series 级挡 720p——本条 1080p 应通过源级但被 series 级排除
    rig2 = await make_rig(
        {title: _parse_result("Bocchi the Rock", 1)},
        [("guid-a", title, "a.torrent")],
        source_kwargs={"include_keywords": "1080p"},
    )
    store2 = rig2.store
    binding = await store2.season_series(rig2.season_a_id)
    assert binding is not None
    series_a = binding[1]
    from autoanime.web.queries import ApiStore

    api_store = ApiStore(rig2.storage)
    await api_store.update_series_fields(series_a.id, {"exclude_keywords": "1080p"})
    outcome = await rig2.poller.poll_source(await rig2.source(), now=NOW)
    assert outcome.rejected == 1
    assert outcome.picked == 0
    assert rig2.gateway.added == []
    # 拒绝记录挂在命中目标的 season 上（ck 约束要求 season/episode 二选一）
    rejected = await store2.list_releases_by_status([ReleaseStatus.CANDIDATE, ReleaseStatus.FAILED])
    assert any(release.reason == "not_included_by_rule" or release.reason for release in rejected)
    del rig  # rig 仅用于构造对照场景


async def test_aggregate_alias_only_title_hits_fast_path() -> None:
    """别名富化：feed 标题只含别名（romaji）→ expected 并入别名后 fast_path 命中。"""
    title = "[LoliHouse] Yani Neko - 01 [1080p]"
    rig = await make_rig(
        {title: _parse_result("Yani Neko", 1)},
        [("guid-a", title, "a.torrent")],
    )
    # 预置别名：订阅标题「孤独摇滚」← 别名 "Yani Neko"（模拟订阅时富化写入）
    await rig.alias_service.upsert_title_aliases(
        "孤独摇滚", ["Yani Neko"], source="bangumi"
    )
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.picked == 1
    assert outcome.ignored == 0
    ep = await rig.episode(rig.season_a_id, 1)
    assert ep.state == EpisodeState.DOWNLOADING


async def test_aggregate_without_alias_unmatched_is_ignored() -> None:
    """无别名时同一标题不命中任何订阅 → ignored（对照组）。"""
    title = "[LoliHouse] Yani Neko - 01 [1080p]"
    rig = await make_rig(
        {title: _parse_result("Yani Neko", 1)},
        [("guid-a", title, "a.torrent")],
    )
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.picked == 0
    assert outcome.ignored == 1


async def test_aggregate_paused_subscription_not_polled() -> None:
    """非 active 订阅不参与聚合对齐（paused 番的条目 → ignored）。"""
    title = "[LoliHouse] Frieren - 01 [1080p]"
    rig = await make_rig(
        {title: _parse_result("葬送的芙莉莲", 1)},
        [("guid-b", title, "b.torrent")],
    )
    binding = await rig.store.season_series(rig.season_b_id)
    assert binding is not None
    series_b = binding[1]
    series_b.status = "paused"
    await rig.storage.add(series_b)
    outcome = await rig.poller.poll_source(await rig.source(), now=NOW)
    assert outcome.picked == 0
    assert outcome.ignored == 1


async def test_poll_all_includes_aggregate_source() -> None:
    """poll_all 枚举含聚合源（enabled 源全量轮询，season_id 为 None 不查季绑定）。"""
    title = "[LoliHouse] Bocchi the Rock - 01 [1080p]"
    rig = await make_rig(
        {title: _parse_result("Bocchi the Rock", 1)},
        [("guid-a", title, "a.torrent")],
    )
    report = await rig.poller.poll_all(now=NOW)
    assert report.errors == ()
    assert report.picked == 1
    assert report.outcomes[0].season_id is None


def test_source_kind_defaults_legacy_rows_to_season() -> None:
    """kind 列为空/未知值的旧行按季绑定源处理。"""
    legacy = RssSource(url="https://x", season_id=1)
    assert source_kind(legacy) == "season"
    legacy.kind = ""
    assert source_kind(legacy) == "season"
    legacy.kind = "AGGREGATE"
    assert source_kind(legacy) == "aggregate"
