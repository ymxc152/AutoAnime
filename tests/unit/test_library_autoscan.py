"""scheduler.library_autoscan 单测：库外下载目录自动入库（全离线）。

覆盖：命中已有订阅的文件归档计数、未命中忽略、已排队跳过、dry_run 只计数、
总开关关闭不扫描。识别/收口均注入 fake，不触真实管线与文件系统归档。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from autoanime.config import Settings
from autoanime.core.enums import Confidence, MediaType, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.core.models import PendingQueue, Season, Series
from autoanime.memory.store import SqliteStorage
from autoanime.scheduler.library_autoscan import LibraryAutoScanner
from autoanime.scheduler.store import LoopStore

NOW = datetime(2026, 9, 13, 12, 0, 0)

_MATCH_NAME = "[LoliHouse] 孤独摇滚 - 01 [1080p][简中].mkv"
_OTHER_NAME = "[SomeGroup] 完全无关的番 - 01 [1080p].mkv"

_OPEN_STORAGES: list[SqliteStorage] = []


class FakeL1:
    """确定性 L1：标题含订阅名的文件解析成功，其余 None。

    needle/title 可子类化覆写（归档命名统一测试用解析名 ≠ 订阅主标题）。
    """

    needle: str = "孤独摇滚"
    title: str = "孤独摇滚"

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        if type(self).needle in raw.name:
            return ParseResult(
                title=self.title,
                season=1,
                episode=1,
                segment=Segment.EPISODE,
                fansub="LoliHouse",
                level=Confidence.HIGH,
                confidence=0.99,
            )
        return None


def _settings(tmp_path: Path, *, enabled: bool = True, dry_run: bool = False) -> Settings:
    settings = Settings()
    settings.download_path = tmp_path / "downloads"
    settings.library_path = tmp_path / "library"
    settings.library_autoscan_enabled = enabled
    settings.dry_run = dry_run
    settings.download_path.mkdir(parents=True, exist_ok=True)
    return settings


async def _rig(
    tmp_path: Path,
    settings: Settings,
    *,
    files: dict[str, str] | None = None,
) -> tuple[LibraryAutoScanner, LoopStore, dict[Path, str]]:
    storage = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await storage.create_all()
    _OPEN_STORAGES.append(storage)
    store = LoopStore(storage)
    await store.create_subscription(
        Series(title_cn="孤独摇滚", media_type=MediaType.TV, status="active"),
        Season(number=1, status="airing"),
        [],
    )
    downloads = settings.download_path
    names = files or {_MATCH_NAME: "x", _OTHER_NAME: "y"}
    for name in names:
        (downloads / name).write_bytes(b"0")
    calls: dict[Path, str] = {}

    async def fake_ingest(files: list[Path], **kwargs: Any) -> dict[Path, str]:
        for f in files:
            calls[f] = "archive"
        return {f: "archive" for f in files}

    scanner = LibraryAutoScanner(
        store,
        storage,
        settings,
        scan_root=downloads,
        l1=FakeL1(),
        ingest_batch=fake_ingest,
        scan_fn=lambda root: (len(names), sorted(root.glob("*.mkv"))),
    )
    return scanner, store, calls


async def test_match_archived_and_unrelated_ignored(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    scanner, _storage, calls = await _rig(tmp_path, settings)
    report = await scanner.scan_and_ingest(now=NOW)
    assert report.total == 2
    assert report.fresh == 2
    assert report.matched == 1
    assert report.archived == 1
    assert report.ignored == 1
    assert [Path(f).name for f in report.matched_files] == [_MATCH_NAME]
    assert list(calls.values()) == ["archive"]


async def test_dry_run_counts_without_ingest(tmp_path: Path) -> None:
    settings = _settings(tmp_path, dry_run=True)
    scanner, _storage, calls = await _rig(tmp_path, settings)
    report = await scanner.scan_and_ingest(now=NOW)
    assert report.dry_run is True
    assert report.matched == 1
    assert report.archived == 0
    assert calls == {}


async def test_disabled_skips_scan(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=False)
    scanner, _storage, calls = await _rig(tmp_path, settings)
    report = await scanner.scan_and_ingest(now=NOW)
    assert report.enabled is False
    assert report.total == 0
    assert calls == {}


async def test_already_pending_name_skipped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    scanner, store, calls = await _rig(tmp_path, settings)
    await store.add_pending(
        PendingQueue(raw_name=_MATCH_NAME, context={}, stage="import", reason="t"),
    )
    report = await scanner.scan_and_ingest(now=NOW)
    assert report.skipped == 1
    assert report.fresh == 1
    assert report.matched == 0
    assert report.ignored == 1
    assert calls == {}


# --------------------------------------- 归档命名统一（批次一遗留修复）


class _CatL1(FakeL1):
    """解析名 = 订阅 romaji 名（≠ 订阅主标题）——真机目录分裂场景。"""

    needle = "Chainsmoker"
    title = "Chainsmoker Cat"


async def test_archive_dir_follows_subscription_naming_title(tmp_path: Path) -> None:
    """命中订阅：归档目录/库条目跟随订阅命名标题，不再按 L1 解析名裂目录。

    全链路走默认收口（真实 L1 管线 + 真实文件归档）：L1 解析出
    "Chainsmoker Cat"，订阅主标题是「尼古喵喵」——修复前归档到
    Chainsmoker Cat/（与 RSS 路径的 尼古喵喵/ 同番裂两目录），修复后
    两条路径都落 尼古喵喵/（naming_title_language=title_cn 回退链）。
    """
    settings = _settings(tmp_path)
    settings.l2_enabled = False
    settings.llm_enabled = False
    settings.reference_enabled = False
    storage = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await storage.create_all()
    _OPEN_STORAGES.append(storage)
    store = LoopStore(storage)
    await store.create_subscription(
        Series(
            title_cn="尼古喵喵",
            title_romaji="Chainsmoker Cat",
            media_type=MediaType.TV,
            status="active",
        ),
        Season(number=1, status="airing"),
        [],
    )
    name = "Chainsmoker.Cat.S01E01.1080p.WEBRip.x264.mkv"
    (settings.download_path / name).write_bytes(b"0")
    scanner = LibraryAutoScanner(
        store,
        storage,
        settings,
        scan_root=settings.download_path,
        l1=_CatL1(),
        scan_fn=lambda root: (1, sorted(root.glob("*.mkv"))),
    )

    report = await scanner.scan_and_ingest(now=NOW)

    assert report.archived == 1
    library = Path(settings.library_path)
    # 目录名 = 订阅命名标题（非 L1 解析名）；文件名沿用 D17 模板
    assert (library / "尼古喵喵" / "Season 01" / "尼古喵喵 - S01E01.1080p.mkv").is_file()
    assert not (library / "Chainsmoker Cat").exists()
    # 库条目聚合在订阅 Series 行（无第二个 series 行）
    rows = await store.list_series()
    assert [(r.title_cn, r.title_romaji) for r in rows] == [("尼古喵喵", "Chainsmoker Cat")]
    seasons = await store.seasons_for_series(rows[0].id)
    episode = await store.episode_for_number(seasons[0].id, 1)
    assert episode is not None and episode.file_path is not None
    assert "尼古喵喵" in episode.file_path


async def test_ingest_passes_matched_series_context(tmp_path: Path) -> None:
    """收口注入侧：文件 → 命中订阅行的映射随 series_by_file 下发。"""
    settings = _settings(tmp_path)
    scanner, store, _calls = await _rig(tmp_path, settings)
    captured: dict[Path, Any] = {}

    async def spy_ingest(files: list[Path], **kwargs: Any) -> dict[Path, str]:
        captured.update(kwargs.get("series_by_file") or {})
        return {f: "archive" for f in files}

    scanner._ingest_batch = spy_ingest  # type: ignore[method-assign]
    await scanner.scan_and_ingest(now=NOW)
    rows = await store.list_series()
    # ORM 行按 id 比对（不同查询的实例不做值相等）
    assert [str(f).endswith(_MATCH_NAME) for f in captured] == [True]
    assert [row.id for row in captured.values()] == [rows[0].id]