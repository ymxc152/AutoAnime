"""T6: 用真实下载快照做 L1 结构级验证。

读取外部快照文件（每行 ``[F] <文件名>`` 或 ``[D] <目录名>``，``#`` 开头为注释），
逐条构造 RawName 并调用 LocalRecognizer，输出稳定性与档位分布统计 JSON。

快照不进仓库；默认从 ``AUTOANIME_L1_SNAPSHOT`` 或仓库上一级 notes 样本目录解析，可用 ``--snapshot`` 覆盖。

RawName 构造约定：
- ``[D]`` 行是目录项，目录名本身就是它的 folder 上下文（folder = name）；
- ``[F]`` 行是文件项，快照未携带父目录信息，folder 为 None。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoanime.core.interfaces import RawName
from autoanime.pipeline.l1_local import LocalRecognizer

_ROOT = Path(__file__).resolve().parent.parent

_SNAPSHOT_RELATIVE_PATH = Path("notes") / "samples" / "z_downloads_snapshot.txt"


def default_snapshot_path() -> Path:
    """Resolve the external snapshot without hard-coding a machine path."""
    from_env = os.getenv("AUTOANIME_L1_SNAPSHOT")
    if from_env:
        return Path(from_env).expanduser()
    return _ROOT.parent / _SNAPSHOT_RELATIVE_PATH

_MAX_FAILED_SAMPLES = 10


@dataclass(frozen=True)
class SnapshotEntry:
    """One ``[D]``/``[F]`` line of the snapshot."""

    kind: str  # "F" = 文件, "D" = 目录
    name: str
    line_number: int


def parse_snapshot_lines(lines: str | Iterable[str]) -> Iterator[SnapshotEntry]:
    """Yield entries from snapshot lines; blank lines and ``#`` comments are skipped.

    ``lines`` may be a whole snapshot text (split internally) or an iterable of lines.
    """
    if isinstance(lines, str):
        lines = lines.splitlines()
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[D] "):
            kind, name = "D", line[4:].strip()
        elif line.startswith("[F] "):
            kind, name = "F", line[4:].strip()
        else:
            continue  # 未知前缀：不是本工具的目标输入，跳过
        if name:
            yield SnapshotEntry(kind=kind, name=name, line_number=line_number)


def to_raw_name(entry: SnapshotEntry) -> RawName:
    """Map a snapshot entry to the RawName the Recognizer contract expects."""
    if entry.kind == "D":
        return RawName(name=entry.name, folder=entry.name)
    return RawName(name=entry.name)


async def validate_entries(entries: Iterable[SnapshotEntry]) -> dict[str, Any]:
    """Run the L1 aggregator over every entry; never raise on a single bad item.

    Returns a JSON-ready statistics report. Per-entry failures are recorded
    (count + first few samples) instead of aborting the run.
    """
    recognizer = LocalRecognizer()
    level_counts = Counter[str]()
    segment_counts = Counter[str]()
    missing_counts = Counter[str]()
    failures: list[dict[str, Any]] = []
    durations: list[float] = []
    parsed = returned_none = failed = 0
    total = 0

    for entry in entries:
        total += 1
        raw = to_raw_name(entry)
        start = time.perf_counter()
        try:
            result = await recognizer.parse(raw)
        except Exception as exc:  # noqa: BLE001 - 单条容错是本验证的核心要求
            failed += 1
            if len(failures) < _MAX_FAILED_SAMPLES:
                failures.append(
                    {
                        "line": entry.line_number,
                        "kind": entry.kind,
                        "name": entry.name,
                        "error": type(exc).__name__,
                    }
                )
            continue
        durations.append(time.perf_counter() - start)
        if result is None:
            returned_none += 1
            continue
        parsed += 1
        level_counts[result.level.value] += 1
        segment_counts[result.segment.value] += 1
        for field_name in result.missing_fields:
            missing_counts[field_name] += 1

    return {
        "total": total,
        "parsed": parsed,
        "returned_none": returned_none,
        "failed": failed,
        "levels": {key: level_counts.get(key, 0) for key in ("high", "medium", "low")},
        "segments": {
            key: segment_counts.get(key, 0) for key in ("season_pack", "episode", "movie")
        },
        "missing_fields": dict(sorted(missing_counts.items())),
        "max_duration_ms": round(max(durations) * 1000, 3) if durations else 0.0,
        "avg_duration_ms": (
            round(sum(durations) / len(durations) * 1000, 3) if durations else 0.0
        ),
        "failed_samples": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="对真实下载快照运行 L1 LocalRecognizer，输出结构级统计 JSON"
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="快照文件路径（默认读取 AUTOANIME_L1_SNAPSHOT 或仓库上一级 notes 样本）",
    )
    args = parser.parse_args(argv)
    args.snapshot = args.snapshot if args.snapshot is not None else default_snapshot_path()
    if not args.snapshot.is_file():
        print(f"snapshot not found: {args.snapshot}", file=sys.stderr)
        return 2
    entries = list(parse_snapshot_lines(args.snapshot.read_text(encoding="utf-8-sig")))
    report = asyncio.run(validate_entries(entries))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
