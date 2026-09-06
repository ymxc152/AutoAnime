"""organize.archive 集成单测（E4b）：完成回调 → 归档/洗版/错配 A/B/C 全链路。

全离线：内存库 + Scripted 管线 + fake 网关 + tmp_path 真文件系统。
"""

from __future__ import annotations

import os
import sqlite3  # noqa: F401 -- 保留：后续集成验收用同步校验
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from autoanime.config import Settings
from autoanime.core.enums import (
    Confidence,
    Decision,
    EpisodeState,
    MediaType,
    ReleaseStatus,
    Segment,
)
from autoanime.core.events import Event
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.core.models import (
    AuditLog,
    BypassList,
    Episode,
    PendingQueue,
    ReleaseRecord,
    Season,
    Series,
)
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.store import SqliteStorage
from autoanime.organize.archive import ArchiveService
from autoanime.pipeline.orchestrator import Orchestrator
from autoanime.scheduler.store import LoopStore

NOW = datetime(2026, 9, 6, 12, 0, 0)

_OPEN_STORAGES: list[SqliteStorage] = []


@pytest.fixture(autouse=True)
async def _close_storages() -> Any:
    yield
    for storage in _OPEN_STORAGES:
        await storage.close()
    _OPEN_STORAGES.clear()


class ScriptedRecognizer:
    def __init__(self, mapping: dict[str, ParseResult]) -> None:
        self._mapping = mapping

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        return self._mapping.get(raw.name)


class FakeGateway:
    def __init__(self, content_dir: Path | None) -> None:
        self._content_dir = content_dir

    async def status(self, torrent_hash: str) -> dict[str, object] | None:
        if self._content_dir is None:
            return None
        return {
            "hash": torrent_hash,
            "state": "completed",
            "progress": 1.0,
            "name": "content",
            "save_path": str(self._content_dir),
            "content_path": str(self._content_dir),
            "size": 1,
        }

    async def completed_hashes(self) -> list[str]:
        return []

    async def add_torrent_bytes(self, data: bytes, *, save_path: str | None = None) -> str:
        return "0" * 40

    async def files(self, torrent_hash: str) -> list[dict[str, object]]:
        return []


class BusRecorder:
    """InMemoryEventBus 的记录替身（只捕事件，不扇出）。"""

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)


def _parse(title: str, episode: int | None, *, season: int | None = 1) -> ParseResult:
    return ParseResult(
        title=title,
        season=season,
        episode=episode,
        segment=Segment.EPISODE,
        fansub="LoliHouse",
        level=Confidence.HIGH,
        confidence=0.99,
    )


class Rig:
    def __init__(
        self,
        store: LoopStore,
        storage: SqliteStorage,
        service: ArchiveService,
        bus: BusRecorder,
        tmp_path: Path,
        season_id: int,
        release: ReleaseRecord,
        episode: Episode,
    ) -> None:
        self.store = store
        self.storage = storage
        self.service = service
        self.bus = bus
        self.tmp_path = tmp_path
        self.season_id = season_id
        self.release = release
        self.episode = episode

    async def episode_row(self, number: int) -> Episode:
        row = await self.store.episode_for_number(self.season_id, number)
        assert row is not None
        return row


async def make_rig(
    mapping: dict[str, ParseResult],
    *,
    files: dict[str, bytes] | None = None,
    episode_state: EpisodeState = EpisodeState.DOWNLOADED,
    quality_score: float | None = None,
    upgraded_count: int = 0,
    release_score: float | None = 11.0,
    file_path: str | None = None,
    content_dir: Path | None = None,
    expected_number: int = 1,
) -> Rig:
    storage = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await storage.create_all()
    _OPEN_STORAGES.append(storage)
    store = LoopStore(storage)
    series = await store.create_subscription(
        Series(title_cn="孤独摇滚", media_type=MediaType.TV, status="active"),
        Season(number=1),
        [Episode(number=n, state=EpisodeState.MISSING) for n in range(1, 6)],
    )
    season = (await store.seasons_for_series(series.id))[0]
    episode = await store.episode_for_number(season.id, expected_number)
    assert episode is not None
    # 期望集走到 DOWNLOADED（首归档）或 ORGANIZED（洗版）
    if episode_state in (EpisodeState.DOWNLOADED, EpisodeState.ORGANIZED):
        await store.transition_episode(episode.id, EpisodeState.DOWNLOADING)
        await store.transition_episode(episode.id, EpisodeState.DOWNLOADED)
        if episode_state is EpisodeState.ORGANIZED:
            await store.transition_episode(episode.id, EpisodeState.ORGANIZED)
            if quality_score is None:
                quality_score = 4.0
            if file_path is None:
                file_path = "library/old.mkv"
    episode.quality_score = quality_score
    episode.upgraded_count = upgraded_count
    episode.file_path = file_path
    await storage.add(episode)

    record = ReleaseRecord(
        episode_id=episode.id,
        torrent_hash="a" * 40,
        score=release_score,
        source_url="guid-a",
    )
    saved = await store.create_release(record)
    assert saved is not None
    await store.transition_release(saved.id, ReleaseStatus.PICKED, now=NOW, decision=Decision.ACCEPTED)
    await store.transition_release(saved.id, ReleaseStatus.DOWNLOADING, now=NOW)
    await store.transition_release(saved.id, ReleaseStatus.COMPLETED, now=NOW)

    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        library_path=Path("library"),
        download_path=Path("downloads"),
        quarantine_path=Path("quarantine"),
    )
    bus = BusRecorder()
    governance = MemoryGovernance(storage)
    service = ArchiveService(
        store,
        Orchestrator(
            recognizer=ScriptedRecognizer(mapping),
            l2_enabled=False,
            audit_sink=governance,
        ),
        FakeGateway(content_dir),
        settings=settings,
        governance=governance,
        bus=bus,
    )
    return Rig(store, storage, service, bus, Path("."), season.id, saved, episode)


def _make_content(tmp_path: Path, name: str, data: bytes = b"v") -> Path:
    content = tmp_path / "content"
    content.mkdir(exist_ok=True)
    (content / name).write_bytes(data)
    (content / (name[: -len(".mkv")] + ".zh.ass")).write_text("subs")
    return content


@pytest.fixture
def tmp_media(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("archive-media")


async def test_fast_path_first_archive_with_subtitle_follow(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """快路径首归档：D17 命名 + D18 字幕跟随 + audit reverse + 事件。"""
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E01.mkv")
    mapping = {"Show - S01E01.mkv": _parse("孤独摇滚", 1)}
    rig = await make_rig(mapping, content_dir=content)
    report = await rig.service.handle_completed(rig.release, None)
    assert report.fast_path_hits == 1
    assert report.archived == 1
    episode = await rig.episode_row(1)
    assert episode.state == EpisodeState.ORGANIZED
    assert episode.file_path is not None
    dst = Path(episode.file_path)
    assert dst.name == "孤独摇滚 - S01E01.SD.mkv"  # 文件名无技术词 → SD
    assert dst.exists()
    # 字幕跟随（D18）
    subtitle = dst.with_name("孤独摇滚 - S01E01.SD.zh.ass")
    assert subtitle.exists()
    # audit 带 reverse instruction（5.4）
    audits = await rig.storage.list(AuditLog)
    organized_rows = [row for row in audits if row.action == "episode.organized"]
    assert organized_rows and "moves" in organized_rows[0].reverse
    # 归档事件
    assert any(event.message == "episode.organized" for event in rig.bus.events)


async def test_fast_path_uses_release_title_quality_label(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "[LoliHouse] Show - S01E01 [Baha 1080p HEVC].mkv")
    mapping = {"[LoliHouse] Show - S01E01 [Baha 1080p HEVC].mkv": _parse("孤独摇滚", 1)}
    rig = await make_rig(mapping, content_dir=content)
    report = await rig.service.handle_completed(rig.release, None)
    assert report.archived == 1
    episode = await rig.episode_row(1)
    assert episode.file_path is not None
    assert Path(episode.file_path).name == "孤独摇滚 - S01E01.1080p.mkv"
    assert episode.quality_score == pytest.approx(11.0)  # release.score 透传


async def test_upgrade_replaces_archive_and_bumps_count(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """洗版：ORGANIZED 集 + 高分候选 → 原子替换 + upgraded_count+1 + 旧文件清理。"""
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E01 [1080p].mkv")
    old_file = tmp_media / "library" / "old.mkv"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_bytes(b"old")
    mapping = {"Show - S01E01 [1080p].mkv": _parse("孤独摇滚", 1)}
    rig = await make_rig(
        mapping,
        content_dir=content,
        episode_state=EpisodeState.ORGANIZED,
        quality_score=4.0,
        upgraded_count=0,
        release_score=11.0,
        file_path=str(old_file),
    )
    report = await rig.service.handle_completed(rig.release, None)
    assert report.upgraded == 1
    episode = await rig.episode_row(1)
    assert episode.state == EpisodeState.ORGANIZED  # UPGRADED→ORGANIZED 两跳
    assert episode.upgraded_count == 1
    assert episode.quality_score == pytest.approx(11.0)
    assert episode.file_path is not None and "1080p" in episode.file_path
    assert not old_file.exists()  # 旧归档名被顶替（D21：下载原件在网关侧不受影响）
    assert any(event.message == "upgrade.completed" for event in rig.bus.events)


async def test_upgrade_refused_keeps_old_file(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E01.mkv")
    old_file = tmp_media / "library" / "old.mkv"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_bytes(b"old")
    mapping = {"Show - S01E01.mkv": _parse("孤独摇滚", 1)}
    rig = await make_rig(
        mapping,
        content_dir=content,
        episode_state=EpisodeState.ORGANIZED,
        quality_score=12.0,
        upgraded_count=0,
        release_score=11.0,  # 12+2 > 11 → 阈值不满足
        file_path=str(old_file),
    )
    report = await rig.service.handle_completed(rig.release, None)
    assert report.upgraded == 0
    episode = await rig.episode_row(1)
    assert episode.quality_score == pytest.approx(12.0)
    assert old_file.exists()
    refreshed = await rig.storage.get(ReleaseRecord, rig.release.id)
    assert refreshed is not None and refreshed.decision == Decision.REJECTED


async def test_mismatch_reattach_branch_a(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 改挂：文件是同番 E02（E02 仍 MISSING）→ 归档到 E02 + release 改挂。"""
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E02.mkv")
    mapping = {"Show - S01E02.mkv": _parse("孤独摇滚", 2)}
    rig = await make_rig(mapping, content_dir=content)
    report = await rig.service.handle_completed(rig.release, None)
    assert report.reattached == 1
    e2 = await rig.episode_row(2)
    assert e2.state == EpisodeState.ORGANIZED
    refreshed = await rig.storage.get(ReleaseRecord, rig.release.id)
    assert refreshed is not None and refreshed.episode_id == e2.id  # expected 载体改挂


async def test_mismatch_branch_c_backfills_and_blacklists(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C 回补：同番但集号不在季内 → 隔离 + 期望集回 MISSING + bypass 拉黑。"""
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E09.mkv")
    mapping = {"Show - S01E09.mkv": _parse("孤独摇滚", 9)}
    rig = await make_rig(mapping, content_dir=content)
    report = await rig.service.handle_completed(rig.release, None)
    assert report.quarantined == 1
    assert report.backfilled == 1
    episode = await rig.episode_row(1)
    assert episode.state == EpisodeState.MISSING  # 回缺等 RSS 自然命中（D15）
    refreshed = await rig.storage.get(ReleaseRecord, rig.release.id)
    assert refreshed is not None and refreshed.decision == Decision.REJECTED
    assert any(event.message == "episode.gap" for event in rig.bus.events)
    # bypass 拉黑（同字幕组+同发布模式防死循环）
    rows = await rig.storage.list(BypassList)
    assert rows


async def test_mismatch_branch_b_pending_queue(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B 人工：解析失败 → 隔离 + pending_queue 附证据链。"""
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "totally-unknown.mkv")
    rig = await make_rig({}, content_dir=content)  # 空 mapping → parse None
    report = await rig.service.handle_completed(rig.release, None)
    assert report.quarantined == 1
    pendings = await rig.storage.list(PendingQueue)
    assert any(row.stage == "mismatch" for row in pendings)
    episode = await rig.episode_row(1)
    assert episode.state == EpisodeState.DOWNLOADED  # B 不回缺（等人工）


async def test_content_dir_unavailable_defers_organize(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网关不可达（content path 缺失）：归档挂起不 crash，集状态不动。"""
    monkeypatch.chdir(tmp_media)
    mapping = {"Show - S01E01.mkv": _parse("孤独摇滚", 1)}
    rig = await make_rig(mapping, content_dir=None)
    report = await rig.service.handle_completed(rig.release, None)
    assert report.archived == 0
    assert any("content path" in note for note in report.notes)
    episode = await rig.episode_row(1)
    assert episode.state == EpisodeState.DOWNLOADED  # 保持待整理


async def test_fast_path_audit_rows_carry_reverse(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """audit reverse instruction 与 E2 rollback 端点共用语义（5.4）。"""
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E01.mkv")
    mapping = {"Show - S01E01.mkv": _parse("孤独摇滚", 1)}
    rig = await make_rig(mapping, content_dir=content)
    await rig.service.handle_completed(rig.release, None)
    audits = await rig.storage.list(AuditLog)
    actions = {row.action for row in audits}
    assert "subscribed_fast_path" in actions  # D13 快路径审计
    assert "episode.organized" in actions


# ------------------------------------------------- id≠number 回归（R3 验收）


async def _make_offset_rig(
    mapping: dict[str, ParseResult],
    *,
    content_dir: Path,
    rejected_on_expected: int = 0,
) -> Rig:
    """期望集 id≠集号 的 rig：先建一个 5 集的占位番，再建目标番——
    目标番的 episode.id 从 6 起，与集号 1-5 错开（多季订阅的真实形态）。
    """
    storage = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await storage.create_all()
    _OPEN_STORAGES.append(storage)
    store = LoopStore(storage)
    # 占位番：占用 episode id 1-5
    await store.create_subscription(
        Series(title_cn="占位番", media_type=MediaType.TV, status="active"),
        Season(number=1),
        [Episode(number=n, state=EpisodeState.MISSING) for n in range(1, 6)],
    )
    series = await store.create_subscription(
        Series(title_cn="错位番", media_type=MediaType.TV, status="active"),
        Season(number=1),
        [Episode(number=n, state=EpisodeState.MISSING) for n in range(1, 6)],
    )
    season = (await store.seasons_for_series(series.id))[0]
    episode = await store.episode_for_number(season.id, 1)
    assert episode is not None
    assert episode.id != episode.number  # 前置：id 与集号确实错开
    await store.transition_episode(episode.id, EpisodeState.DOWNLOADING)
    await store.transition_episode(episode.id, EpisodeState.DOWNLOADED)
    for _ in range(rejected_on_expected):
        filler = ReleaseRecord(
            episode_id=episode.id,
            torrent_hash=_random_hash(),
            decision=Decision.REJECTED,
            reason="mismatch C_backfill: prior",
            source_url=f"guid-{_random_hash()}",
        )
        saved = await store.create_release(filler)
        assert saved is not None
    record = ReleaseRecord(
        episode_id=episode.id,
        torrent_hash="a" * 40,
        score=11.0,
        source_url="guid-a",
    )
    saved = await store.create_release(record)
    assert saved is not None
    await store.transition_release(saved.id, ReleaseStatus.PICKED, now=NOW, decision=Decision.ACCEPTED)
    await store.transition_release(saved.id, ReleaseStatus.DOWNLOADING, now=NOW)
    await store.transition_release(saved.id, ReleaseStatus.COMPLETED, now=NOW)
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        library_path=Path("library"),
        download_path=Path("downloads"),
        quarantine_path=Path("quarantine"),
    )
    governance = MemoryGovernance(storage)
    bus = BusRecorder()
    service = ArchiveService(
        store,
        Orchestrator(recognizer=ScriptedRecognizer(mapping), l2_enabled=False, audit_sink=governance),
        FakeGateway(content_dir),
        settings=settings,
        governance=governance,
        bus=bus,
    )
    return Rig(store, storage, service, bus, Path("."), season.id, saved, episode)


def _random_hash() -> str:
    import hashlib
    import uuid as _uuid

    return hashlib.sha1(_uuid.uuid4().bytes).hexdigest()


async def test_mismatch_reattach_when_episode_ids_offset_from_numbers(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归（R3 验收）：episode.id≠集号时 A 改挂必须按 id 定位期望集。

    修复前 _process_file 把 expected.episode_number 当 episode id 传给
    episode_context——多季订阅下查到占位番的行，改挂目标取自已错位的季。
    """
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E03.mkv")
    mapping = {"Show - S01E03.mkv": _parse("错位番", 3)}
    rig = await _make_offset_rig(mapping, content_dir=content)
    report = await rig.service.handle_completed(rig.release, None)
    assert report.reattached == 1
    e3 = await rig.episode_row(3)  # 目标番的 E03（id≠3）
    assert e3.state == EpisodeState.ORGANIZED
    assert e3.file_path is not None and "错位番" in e3.file_path
    refreshed = await rig.storage.get(ReleaseRecord, rig.release.id)
    assert refreshed is not None and refreshed.episode_id == e3.id
    # R3 实测缺陷回归：期望集（内容已改挂他集）回 MISSING + 缺口事件，
    # 不停留在无文件的 DOWNLOADED（挡住回补）
    e1 = await rig.episode_row(1)
    assert e1.state == EpisodeState.MISSING
    assert any(
        event.message == "episode.gap" and event.payload.get("reason") == "mismatch_reattach"
        for event in rig.bus.events
    )


async def test_mismatch_budget_counts_expected_episode_releases_when_ids_offset(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归（R3 验收）：回补预算按 expected 集的 id 统计 rejected release。

    修复前 find_releases_by_episode(expected.episode_number) 按集号查——
    id 错位时数到占位番的 release，预算恒 0，永不转人工。
    """
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E09.mkv")
    mapping = {"Show - S01E09.mkv": _parse("错位番", 9)}  # 目标番只有 5 集
    rig = await _make_offset_rig(mapping, content_dir=content, rejected_on_expected=2)
    report = await rig.service.handle_completed(rig.release, None)
    assert report.quarantined == 1
    # 预算 2 已被同集的两次 rejected 用尽 → 转人工（不再回补）
    pendings = await rig.storage.list(PendingQueue)
    assert any(
        row.stage == "mismatch" and "C_budget_exhausted" in str(row.context.get("branch"))
        for row in pendings
    )
    episode = await rig.episode_row(1)
    assert episode.state == EpisodeState.DOWNLOADED  # 预算用尽不回缺


# ----------------------------------------------- 多集种子死锁回归（P0）


async def test_multi_episode_torrent_reattaches_and_still_archives_expected(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0 回归：多集种子（ep3+ep5，release 挂 ep5）——A 改挂后 ep5 仍要归档。

    修复前：ep3 先走 A 改挂 → release 改挂 + 期望集 ep5 被立即打回 MISSING；
    随后 ep5.mkv 快路径按 stale release.episode_id 路由读到 MISSING →
    "nothing to organize"——ep5 永远 MISSING、文件滞留、同 hash 永不重下。
    """
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E03.mkv")
    (content / "Show - S01E05.mkv").write_bytes(b"v5")
    mapping = {
        "Show - S01E03.mkv": _parse("孤独摇滚", 3),
        "Show - S01E05.mkv": _parse("孤独摇滚", 5),
    }
    rig = await make_rig(mapping, content_dir=content, expected_number=5)
    report = await rig.service.handle_completed(rig.release, None)
    # 两个文件各自归位
    e3 = await rig.episode_row(3)
    e5 = await rig.episode_row(5)
    assert e3.state == EpisodeState.ORGANIZED
    assert e5.state == EpisodeState.ORGANIZED  # 修复前：MISSING + 文件滞留
    assert e5.file_path is not None and Path(e5.file_path).exists()
    assert report.reattached == 1
    assert report.archived == 2  # ep3 改挂归档 + ep5 快路径归档
    refreshed = await rig.storage.get(ReleaseRecord, rig.release.id)
    assert refreshed is not None and refreshed.episode_id == e3.id  # 改挂落库
    # 不能出现"放弃整理"的 note（修复前的死锁症状）
    assert not any("nothing to organize" in note for note in report.notes)


async def test_multi_episode_torrent_c_backfill_deferred_and_fires_once(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0 回归：多集种子（ep3+ep9，release 挂 ep5）——回缺延后到全部文件处理完。

    ep3 改挂归档、ep9（集外）C 隔离；ep5 内容确实不在种内 → 全部文件处理
    完后一次性回缺（只回缺一次、只发一次缺口事件）。
    """
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E03.mkv")
    (content / "Show - S01E09.mkv").write_bytes(b"v9")
    mapping = {
        "Show - S01E03.mkv": _parse("孤独摇滚", 3),
        "Show - S01E09.mkv": _parse("孤独摇滚", 9),  # 集外 → C
    }
    rig = await make_rig(mapping, content_dir=content, expected_number=5)
    report = await rig.service.handle_completed(rig.release, None)
    e3 = await rig.episode_row(3)
    e5 = await rig.episode_row(5)
    assert e3.state == EpisodeState.ORGANIZED  # 改挂照常
    assert e5.state == EpisodeState.MISSING  # 内容不在种内 → 回缺等 RSS
    assert report.reattached == 1
    assert report.backfilled == 1
    gap_events = [event for event in rig.bus.events if event.message == "episode.gap"]
    assert len(gap_events) == 1  # 只回缺一次、只发一次缺口事件
    assert gap_events[0].payload.get("gap") == [5]
    refreshed = await rig.storage.get(ReleaseRecord, rig.release.id)
    assert refreshed is not None and refreshed.episode_id == e3.id


async def test_multi_episode_torrent_expected_file_covers_no_backfill(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0 回归：多集种子含期望集文件 + C 集外文件 → 期望集已归档绝不回缺。

    修复前：ep9 的 C 分支会把已 ORGANIZED 的期望集再拉一次回缺（转移被拒
    但缺口事件照发，谎报缺口）。
    """
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E05.mkv")
    (content / "Show - S01E09.mkv").write_bytes(b"v9")
    mapping = {
        "Show - S01E05.mkv": _parse("孤独摇滚", 5),
        "Show - S01E09.mkv": _parse("孤独摇滚", 9),  # 集外 → C
    }
    rig = await make_rig(mapping, content_dir=content, expected_number=5)
    report = await rig.service.handle_completed(rig.release, None)
    e5 = await rig.episode_row(5)
    assert e5.state == EpisodeState.ORGANIZED  # 期望集内容已归档
    assert e5.file_path is not None and Path(e5.file_path).exists()
    assert report.quarantined == 1  # ep9 照常隔离
    assert report.backfilled == 0  # 有覆盖 → 不回缺
    assert not [event for event in rig.bus.events if event.message == "episode.gap"]


async def test_single_episode_torrent_behavior_unchanged(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0 钉死：单集种子（快路径首归档 + A 改挂 + C 回补）行为逐字节不变。"""
    monkeypatch.chdir(tmp_media)
    # 快路径首归档
    content = _make_content(tmp_media, "Show - S01E01.mkv")
    mapping = {"Show - S01E01.mkv": _parse("孤独摇滚", 1)}
    rig = await make_rig(mapping, content_dir=content)
    report = await rig.service.handle_completed(rig.release, None)
    assert report.fast_path_hits == 1 and report.archived == 1
    episode = await rig.episode_row(1)
    assert episode.state == EpisodeState.ORGANIZED
    # A 改挂：期望集回缺 + 缺口事件 reason=mismatch_reattach（原语义）
    content2 = _make_content(tmp_media, "Show - S01E02.mkv")
    (content2 / "Show - S01E01.mkv").unlink(missing_ok=True)
    (content2 / "Show - S01E01.zh.ass").unlink(missing_ok=True)
    mapping2 = {"Show - S01E02.mkv": _parse("孤独摇滚", 2)}
    rig2 = await make_rig(mapping2, content_dir=content2)
    report2 = await rig2.service.handle_completed(rig2.release, None)
    assert report2.reattached == 1
    e1 = await rig2.episode_row(1)
    assert e1.state == EpisodeState.MISSING
    assert any(
        event.message == "episode.gap"
        and event.payload.get("reason") == "mismatch_reattach"
        for event in rig2.bus.events
    )
    # C 回补：回缺 + 缺口事件（原语义）
    content3 = _make_content(tmp_media, "Show - S01E09.mkv")
    (content3 / "Show - S01E02.mkv").unlink()
    (content3 / "Show - S01E02.zh.ass").unlink()
    mapping3 = {"Show - S01E09.mkv": _parse("孤独摇滚", 9)}
    rig3 = await make_rig(mapping3, content_dir=content3)
    report3 = await rig3.service.handle_completed(rig3.release, None)
    assert report3.backfilled == 1
    e1b = await rig3.episode_row(1)
    assert e1b.state == EpisodeState.MISSING
    assert any(event.message == "episode.gap" for event in rig3.bus.events)


# ------------------------------------- D21 目标位守卫（订阅归档路径，P1）


async def test_subscribe_archive_respects_dst_exists_guard(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 回归：订阅归档目标位已有不同内容 → skip，不静默覆盖（洗版闸门管辖）。

    修复前：_archive_episode 规划后直接 execute_transfer（os.replace 无条件
    落位），与 import/confirm 归档不一致，绕过洗版评分闸门。
    """
    monkeypatch.chdir(tmp_media)
    dst = tmp_media / "library" / "孤独摇滚" / "Season 01" / "孤独摇滚 - S01E01.SD.mkv"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"already-in-library")
    content = _make_content(tmp_media, "Show - S01E01.mkv")
    mapping = {"Show - S01E01.mkv": _parse("孤独摇滚", 1)}
    rig = await make_rig(mapping, content_dir=content)
    report = await rig.service.handle_completed(rig.release, None)
    assert report.archived == 0
    assert any("dst-exists-upgrade-gated" in note for note in report.notes)
    episode = await rig.episode_row(1)
    assert episode.state == EpisodeState.DOWNLOADED  # 未被覆盖、未谎报 ORGANIZED
    assert dst.read_bytes() == b"already-in-library"  # 库内旧内容原样
    audits = await rig.storage.list(AuditLog)
    assert any(
        row.action == "organize.skipped"
        and row.instruction.get("reason") == "dst-exists-upgrade-gated"
        for row in audits
    )


async def test_subscribe_archive_dst_same_content_skips_idempotent(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """订阅归档目标位已有同内容文件（同 inode）→ skip same-content，幂等。"""
    monkeypatch.chdir(tmp_media)
    dst = tmp_media / "library" / "孤独摇滚" / "Season 01" / "孤独摇滚 - S01E01.SD.mkv"
    dst.parent.mkdir(parents=True, exist_ok=True)
    content = _make_content(tmp_media, "Show - S01E01.mkv")
    os.link(content / "Show - S01E01.mkv", dst)  # 同 inode = 内容已在库
    mapping = {"Show - S01E01.mkv": _parse("孤独摇滚", 1)}
    rig = await make_rig(mapping, content_dir=content)
    report = await rig.service.handle_completed(rig.release, None)
    assert report.archived == 0
    assert any("dst-exists-same-content" in note for note in report.notes)
    episode = await rig.episode_row(1)
    assert episode.state == EpisodeState.DOWNLOADED


async def test_upgrade_with_dst_exists_still_replaces(
    tmp_media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 钉死：洗版路径（评分闸门通过）显式绕行守卫——目标位同路径仍替换。"""
    monkeypatch.chdir(tmp_media)
    content = _make_content(tmp_media, "Show - S01E01 [1080p].mkv")
    # 与服务层 plan 的路径形态一致（rig 的 library_path 是相对路径）
    rel_dst = Path("library") / "孤独摇滚" / "Season 01" / "孤独摇滚 - S01E01.1080p.mkv"
    dst = tmp_media / rel_dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"old-content")
    mapping = {"Show - S01E01 [1080p].mkv": _parse("孤独摇滚", 1)}
    rig = await make_rig(
        mapping,
        content_dir=content,
        episode_state=EpisodeState.ORGANIZED,
        quality_score=4.0,
        release_score=11.0,
        file_path=str(rel_dst),
    )
    report = await rig.service.handle_completed(rig.release, None)
    assert report.upgraded == 1
    episode = await rig.episode_row(1)
    assert episode.upgraded_count == 1
    assert episode.quality_score == pytest.approx(11.0)
    assert dst.read_bytes() == b"v"  # 新内容落位（硬链接同源）
