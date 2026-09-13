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
from autoanime.core.models import Episode, PendingQueue, Season, Series
from autoanime.memory.store import SqliteStorage
from autoanime.scheduler.library_autoscan import LibraryAutoScanner
from autoanime.scheduler.store import LoopStore

NOW = datetime(2026, 9, 13, 12, 0, 0)

_MATCH_NAME = "[LoliHouse] 孤独摇滚 - 01 [1080p][简中].mkv"
_OTHER_NAME = "[SomeGroup] 完全无关的番 - 01 [1080p].mkv"

_OPEN_STORAGES: list[SqliteStorage] = []


class FakeL1:
    """确定性 L1：标题含订阅名的文件解析成功，其余 None。"""

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        if "孤独摇滚" in raw.name:
            return ParseResult(
                title="孤独摇滚",
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
