"""organize.archive 集成单测（E4b）：完成回调 → 归档/洗版/错配 A/B/C 全链路。

全离线：内存库 + Scripted 管线 + fake 网关 + tmp_path 真文件系统。
"""

from __future__ import annotations

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
    episode = await store.episode_for_number(season.id, 1)
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
