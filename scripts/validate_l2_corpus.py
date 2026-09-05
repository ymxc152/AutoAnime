"""T6: 用真实下载快照做 L2 结构级两遍验证（冷启动 → 模拟确认学习 → 复测命中）。

对同一条快照跑两遍完整管线（L1 → L2），中间用模拟确认学习填充 parse_memory：

- pass1（冷启动）：空库全量跑 Orchestrator，统计 L1 档位分布、L2 未命中率、
  剧目级 key 数量（对照粗原型 458）；
- learn：把 pass1 的 MEDIUM 结果按剧目级 key（``level1_key``）聚类，簇内
  一致性补齐 season / fansub 后作为「用户确认」，走 T2 ``learn_confirmation``
  入口写入 parse_memory；
- pass2（复测）：全量重跑，统计 L2 命中率（对照粗原型「309 key 覆盖 94.3%」）、
  MEDIUM→HIGH 迁移、missing_fields 收敛与每条耗时。

附加观察（只记录，不修改 L1/L2）：

- PR3 遗留 returned_none 样本在 L2 后的行为；
- season-residue 疑似误杀样本的 L2 兜底情况；
- 「HIGH 也查记忆」的潜在额外命中率（离线估算，不实现进路由）。

快照不进仓库；解析约定与 ``validate_l1_corpus.py`` 一致（``[F]``/``[D]`` 前缀、
``#`` 注释行）。单条异常容错：记录 failed 继续跑，统计中体现 failed 数量。

性能说明：``lookup.py``/``learn.py`` 的 ``find_parse_memory`` 是 Python 侧全表
过滤，在 2600 条 × 每条两次查询的规模下代价过高；本脚本用带批量预载索引的
Store 子类（命中语义与 ``uq_parse_memory_key`` 唯一约束一致），L1/L2 模块
本身零改动。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from autoanime.core.enums import Confidence, MemoryStatus
from autoanime.core.interfaces import ParseResult, RawName
from autoanime.core.models import BypassList, ParseMemory
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.learn import StorageMemoryAccess, learn_confirmation
from autoanime.memory.lookup import StorageMemoryStore, lookup_memory
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l1.confidence import confidence_for
from autoanime.pipeline.l1_local import _SEASON_RESIDUE_RE  # 私有导入：与 L1 误杀规则保持同源
from autoanime.pipeline.l2.keys import level1_key, level2_key
from autoanime.pipeline.l2.trust import trust_score
from autoanime.pipeline.orchestrator import ROUTE_L3, ROUTE_MEMORY, Orchestrator

_ROOT = Path(__file__).resolve().parent.parent

_SNAPSHOT_RELATIVE_PATH = Path("notes") / "samples" / "z_downloads_snapshot.txt"

_MAX_FAILED_SAMPLES = 10

_FILLABLE_FIELDS = ("title", "season", "episode", "segment", "fansub")

_PROTECTED_EVIDENCE = frozenset({"name", "folder"})

_MEMORY_EVIDENCE = "memory"


def default_snapshot_path() -> Path:
    """Resolve the external snapshot without hard-coding a machine path."""
    from_env = os.getenv("AUTOANIME_L2_SNAPSHOT")
    if from_env:
        return Path(from_env).expanduser()
    for directory in (_ROOT, *_ROOT.parents):
        candidate = directory / _SNAPSHOT_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    return _ROOT.parent / _SNAPSHOT_RELATIVE_PATH


@dataclass(frozen=True)
class SnapshotEntry:
    """One ``[D]``/``[F]`` line of the snapshot."""

    kind: str  # "F" = 文件, "D" = 目录
    name: str
    line_number: int


def parse_snapshot_lines(lines: str | Iterable[str]) -> Iterator[SnapshotEntry]:
    """Yield entries from snapshot lines; blank lines and ``#`` comments are skipped."""
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
    """``[D]`` 目录名即 folder 上下文；``[F]`` 快照无父目录信息，folder 为 None。"""
    if entry.kind == "D":
        return RawName(name=entry.name, folder=entry.name)
    return RawName(name=entry.name)


# ---------------------------------------------------------------------------
# 带批量预载索引的 store 适配（避免查询/学习侧的全表 Python 过滤）
# ---------------------------------------------------------------------------


class _IndexedMemoryStore(StorageMemoryStore):
    """查询侧：``StorageMemoryStore`` + 预载索引。

    ``find_parse_memory``/``has_bypass`` 从预载 dict 直接命中（语义等价于
    按 ``(key_level, key_hash)``/``pattern_hash`` 的唯一查找）；``record_hit``
    与 ``record_correction`` 完全复用基类实现。
    """

    def __init__(self, storage: SqliteStorage) -> None:
        super().__init__(storage)
        self._index: dict[tuple[int, str], Any] = {}
        self._bypassed: frozenset[str] = frozenset()

    async def reload(self) -> int:
        """Re-read every row from the DB into the index (一次预载)."""
        rows = await self._storage.list(ParseMemory)
        self._index = {(row.key_level, row.key_hash): row for row in rows}
        self._bypassed = frozenset(
            row.pattern_hash for row in await self._storage.list(BypassList)
        )
        return len(rows)

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None:
        return self._index.get((key_level, key_hash))

    async def has_bypass(self, pattern_hash: str) -> bool:
        return pattern_hash in self._bypassed


class _IndexedMemoryAccess(StorageMemoryAccess):
    """学习侧：``StorageMemoryAccess`` + 预载索引（``learn_confirmation`` 入口不变）。"""

    def __init__(self, storage: Any) -> None:
        super().__init__(storage)
        self._index: dict[tuple[int, str], Any] = {}
        self._bypassed: frozenset[str] = frozenset()

    async def reload(self) -> int:
        rows = await self._storage.list(ParseMemory)
        self._index = {(row.key_level, row.key_hash): row for row in rows}
        self._bypassed = frozenset(
            row.pattern_hash for row in await self._storage.list(BypassList)
        )
        return len(rows)

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None:
        return self._index.get((key_level, key_hash))

    async def add(self, parse_memory: Any) -> None:
        await self._storage.add(parse_memory)
        self._index[(parse_memory.key_level, parse_memory.key_hash)] = parse_memory

    async def has_bypass(self, pattern_hash: str) -> bool:
        return pattern_hash in self._bypassed


# ---------------------------------------------------------------------------
# 两遍跑批
# ---------------------------------------------------------------------------


@dataclass
class PassRecord:
    """One entry's outcome in one pass."""

    entry: SnapshotEntry
    raw: RawName
    result: ParseResult | None  # 该遍的生效结果（pass2 中可能已被 L2 融合）
    route: str
    l2_applied: bool
    degraded: bool
    duration_s: float
    error: str | None = None


async def run_pass(
    entries: Sequence[SnapshotEntry], orchestrator: Orchestrator
) -> list[PassRecord]:
    """Run every entry once; a single entry raising never aborts the pass."""
    records: list[PassRecord] = []
    for entry in entries:
        raw = to_raw_name(entry)
        start = time.perf_counter()
        try:
            outcome = await orchestrator.process(raw)
        except Exception as exc:  # noqa: BLE001 -- 单条容错是本验证的核心要求
            records.append(
                PassRecord(
                    entry=entry,
                    raw=raw,
                    result=None,
                    route="error",
                    l2_applied=False,
                    degraded=False,
                    duration_s=time.perf_counter() - start,
                    error=type(exc).__name__,
                )
            )
            continue
        records.append(
            PassRecord(
                entry=entry,
                raw=raw,
                result=outcome.result,
                route=outcome.route,
                l2_applied=outcome.l2_applied,
                degraded=outcome.degraded,
                duration_s=time.perf_counter() - start,
            )
        )
    return records


def _duration_stats(durations: Sequence[float]) -> dict[str, float]:
    if not durations:
        return {"avg_ms": 0.0, "max_ms": 0.0, "total_s": 0.0}
    return {
        "avg_ms": round(sum(durations) / len(durations) * 1000, 3),
        "max_ms": round(max(durations) * 1000, 3),
        "total_s": round(sum(durations), 3),
    }


def pass_stats(records: Sequence[PassRecord]) -> dict[str, Any]:
    """Deterministic statistics block for one pass."""
    ok_records = [r for r in records if r.error is None]
    parsed_results = [r.result for r in ok_records if r.result is not None]
    medium_results = [r for r in parsed_results if r.level is Confidence.MEDIUM]
    level_counts: Counter[str] = Counter(r.level.value for r in parsed_results)
    segment_counts: Counter[str] = Counter(r.segment.value for r in parsed_results)
    missing_counts: Counter[str] = Counter(
        f for r in parsed_results for f in r.missing_fields
    )
    route_counts: Counter[str] = Counter(r.route for r in ok_records)
    failed = [r for r in records if r.error is not None]
    return {
        "parsed": len(parsed_results),
        "returned_none": sum(1 for r in ok_records if r.result is None),
        "failed": len(failed),
        "failed_samples": [
            {
                "line": r.entry.line_number,
                "kind": r.entry.kind,
                "name": r.entry.name,
                "error": r.error,
            }
            for r in failed[:_MAX_FAILED_SAMPLES]
        ],
        "routes": {
            "archive": route_counts.get("archive", 0),
            "memory": route_counts.get(ROUTE_MEMORY, 0),
            "l3": route_counts.get(ROUTE_L3, 0),
        },
        "levels": {
            key: level_counts.get(key, 0) for key in ("high", "medium", "low")
        },
        "segments": {
            key: segment_counts.get(key, 0)
            for key in ("season_pack", "episode", "movie")
        },
        "l2_eligible": len(medium_results),
        "degraded": sum(1 for r in records if r.degraded),
        "missing_fields": dict(sorted(missing_counts.items())),
        "missing_fields_total": sum(missing_counts.values()),
        "level1_keys_all": len({level1_key(r.title) for r in parsed_results}),
        "level1_keys_medium": len({level1_key(r.title) for r in medium_results}),
        "level2_keys_medium": len(
            {level2_key(r.title, r.season, r.episode, r.fansub) for r in medium_results}
        ),
        "duration_ms": _duration_stats([r.duration_s for r in ok_records]),
    }


# ---------------------------------------------------------------------------
# 模拟确认学习：按剧目级 key 聚类
# ---------------------------------------------------------------------------


@dataclass
class LearnPlan:
    """The confirmation inputs derived from pass1, plus plan-level counters."""

    confirmations: list[tuple[SnapshotEntry, ParseResult]] = field(default_factory=list)
    clusters: int = 0
    filled_season: int = 0
    filled_fansub: int = 0


def _unique_mode(values: Counter[str]) -> str | None:
    """The value behind a strictly unique maximum count, else None."""
    if not values:
        return None
    top = max(values.values())
    winners = sorted(value for value, count in values.items() if count == top)
    return winners[0] if len(winners) == 1 else None


def build_learn_plan(records: Sequence[PassRecord]) -> LearnPlan:
    """Cluster pass1 MEDIUM results by series key and derive confirmations.

    模拟语义：确认 = 该条 L1 结果 + 簇内一致性补齐缺失字段；确认级别视为
    HIGH（用户拍板），evidence 置空。

    - season：簇内所有非空 season 完全一致才补（跨季混合的簇不补，避免污染）；
    - fansub：簇内出现次数严格唯一的众数才补；
    - title/episode/segment 保持该条 L1 自己的值。
    """
    medium: list[tuple[SnapshotEntry, ParseResult]] = [
        (r.entry, r.result)
        for r in records
        if r.error is None and r.result is not None and r.result.level is Confidence.MEDIUM
    ]
    clusters: dict[str, list[tuple[SnapshotEntry, ParseResult]]] = {}
    for entry, result in medium:
        clusters.setdefault(level1_key(result.title), []).append((entry, result))

    plan = LearnPlan(clusters=len(clusters))
    for key in sorted(clusters):
        members = clusters[key]
        seasons = {result.season for _, result in members if result.season is not None}
        consensus_season = next(iter(seasons)) if len(seasons) == 1 else None
        fansub_counts: Counter[str] = Counter(
            result.fansub for _, result in members if result.fansub
        )
        consensus_fansub = _unique_mode(fansub_counts)
        for entry, result in sorted(members, key=lambda pair: pair[0].line_number):
            confirmed_season = (
                result.season if result.season is not None else consensus_season
            )
            confirmed_fansub = (
                result.fansub if result.fansub else consensus_fansub
            )
            if result.season is None and confirmed_season is not None:
                plan.filled_season += 1
            if not result.fansub and confirmed_fansub:
                plan.filled_fansub += 1
            confirmed = replace(
                result,
                season=confirmed_season,
                fansub=confirmed_fansub,
                level=Confidence.HIGH,
                confidence=confidence_for(Confidence.HIGH),
                missing_fields=(),
                evidence={},
            )
            plan.confirmations.append((entry, confirmed))
    return plan


async def run_learn(
    plan: LearnPlan, access: _IndexedMemoryAccess, storage: SqliteStorage
) -> dict[str, Any]:
    """Learn every confirmation through the T2 ``learn_confirmation`` entry."""
    learn_failed = 0
    learn_failed_samples: list[dict[str, Any]] = []
    bypassed = 0
    for entry, confirmed in plan.confirmations:
        try:
            outcome = await learn_confirmation(
                access, confirmed=confirmed, raw_name=entry.name, bypass_lookup=access
            )
        except Exception as exc:  # noqa: BLE001 -- 单条容错
            learn_failed += 1
            if len(learn_failed_samples) < _MAX_FAILED_SAMPLES:
                learn_failed_samples.append(
                    {
                        "line": entry.line_number,
                        "name": entry.name,
                        "error": type(exc).__name__,
                    }
                )
            continue
        if outcome.bypassed:
            bypassed += 1

    stats: dict[str, Any] = {
        "confirmations": len(plan.confirmations),
        "clusters": plan.clusters,
        "filled_season": plan.filled_season,
        "filled_fansub": plan.filled_fansub,
        "bypassed": bypassed,
        "learn_failed": learn_failed,
        "learn_failed_samples": learn_failed_samples,
    }
    stats.update(memory_stats(await storage.list(ParseMemory)))
    return stats


def memory_stats(rows: Sequence[Any]) -> dict[str, Any]:
    """Row counts, trust distribution and status counts over ParseMemory rows."""
    trust_buckets: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for row in rows:
        score = trust_score(row.hit_count, row.corrected_count)
        if score == 1.0:
            bucket = "1.0"
        elif score >= 0.8:
            bucket = "0.8_to_1.0"
        elif score >= 0.5:
            bucket = "0.5_to_0.8"
        else:
            bucket = "below_0.5"
        trust_buckets[bucket] += 1
        status_counts[getattr(row.status, "value", str(row.status))] += 1
    return {
        "rows": len(rows),
        "rows_series": sum(1 for row in rows if row.key_level == 1),
        "rows_exact": sum(1 for row in rows if row.key_level == 2),
        "trust_distribution": {
            key: trust_buckets.get(key, 0)
            for key in ("1.0", "0.8_to_1.0", "0.5_to_0.8", "below_0.5")
        },
        "status_counts": {
            key: status_counts.get(key, 0)
            for key in (MemoryStatus.ACTIVE.value, MemoryStatus.PENDING.value, MemoryStatus.DEPRECATED.value)
        },
        "hit_count_sum": sum(row.hit_count for row in rows),
        "corrected_count_sum": sum(row.corrected_count for row in rows),
    }


# ---------------------------------------------------------------------------
# 附加观察（只记录，不改 L1/L2）
# ---------------------------------------------------------------------------


async def high_lookup_estimate(
    records: Sequence[PassRecord], store: StorageMemoryStore
) -> dict[str, Any]:
    """Offline estimate of "HIGH also queries memory" (路由不实现，只估算).

    对 pass1 的每条 HIGH 结果跑与查询侧相同的两级 lookup（``lookup_memory``
    只读、不记 hit），统计有多少条能找到参与行、其中多少条至少能补一个字段。
    """
    matched = 0
    would_fill = 0
    fill_fields: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    high_records = [
        r
        for r in records
        if r.error is None and r.result is not None and r.result.level is Confidence.HIGH
    ]
    for record in high_records:
        result = record.result
        if result is None:
            continue
        match = await lookup_memory(result, store)
        if match is None:
            continue
        matched += 1
        fills = [
            field_name
            for field_name in _FILLABLE_FIELDS
            if getattr(match.hit, field_name) is not None
            and getattr(result, field_name) is None
            and result.evidence.get(field_name) not in _PROTECTED_EVIDENCE
        ]
        if fills:
            would_fill += 1
            fill_fields.update(fills)
            if len(samples) < _MAX_FAILED_SAMPLES:
                samples.append(
                    {
                        "line": record.entry.line_number,
                        "name": record.entry.name,
                        "fills": fills,
                    }
                )
    return {
        "high_results": len(high_records),
        "with_memory_match": matched,
        "would_fill_field": would_fill,
        "fill_fields": dict(sorted(fill_fields.items())),
        "samples": samples,
        "note": "离线估算：lookup 只读不记 hit；fused_level 对 HIGH 无级别迁移，仅可能补字段",
    }


def season_residue_observation(
    pass1_records: Sequence[PassRecord], pass2_records: Sequence[PassRecord]
) -> dict[str, Any]:
    """season-residue 疑似误杀样本的观察（只记录）。

    判定（外部可观测代理）：raw name 命中 L1 的 season-residue 正则，但 pass1
    结果为 None（候选被整体丢弃）或 season 为空（含 season 标记的候选被丢弃）。
    L2 兜底 = pass2 中 season 被记忆补齐（evidence == memory）。
    """
    pass2_by_line = {r.entry.line_number: r for r in pass2_records}
    candidates = [
        r
        for r in pass1_records
        if r.error is None and _SEASON_RESIDUE_RE.search(r.raw.name)
    ]
    l1_none = [r for r in candidates if r.result is None]
    season_missing = [
        r for r in candidates if r.result is not None and r.result.season is None
    ]
    rescued: list[dict[str, Any]] = []
    for record in season_missing:
        after = pass2_by_line.get(record.entry.line_number)
        if (
            after is not None
            and after.result is not None
            and after.result.season is not None
            and after.result.evidence.get("season") == _MEMORY_EVIDENCE
        ):
            if len(rescued) < _MAX_FAILED_SAMPLES:
                rescued.append(
                    {
                        "line": record.entry.line_number,
                        "name": record.entry.name,
                        "pass2_season": after.result.season,
                    }
                )
    return {
        "candidates": len(candidates),
        "l1_none": len(l1_none),
        "l1_none_pass2_still_none": sum(
            1
            for r in l1_none
            if (after := pass2_by_line.get(r.entry.line_number)) is not None
            and after.result is None
        ),
        "l1_season_missing": len(season_missing),
        "l2_season_rescued": len(rescued),
        "rescued_samples": rescued,
    }


def returned_none_observation(
    pass1_records: Sequence[PassRecord], pass2_records: Sequence[PassRecord]
) -> dict[str, Any]:
    """PR3 遗留 returned_none 样本在 L2 后的行为（只记录）。"""
    pass2_by_line = {r.entry.line_number: r for r in pass2_records}
    none_records = [r for r in pass1_records if r.error is None and r.result is None]
    return {
        "count": len(none_records),
        "pass2_still_none": sum(
            1
            for r in none_records
            if (after := pass2_by_line.get(r.entry.line_number)) is not None
            and after.result is None
            and after.route == ROUTE_L3
        ),
        "samples": [
            {"line": r.entry.line_number, "kind": r.entry.kind, "name": r.entry.name}
            for r in none_records[:_MAX_FAILED_SAMPLES]
        ],
    }


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------


async def run_two_pass(
    entries: Sequence[SnapshotEntry], db_url: str = "sqlite+aiosqlite:///:memory:"
) -> dict[str, Any]:
    """Cold-start pass, simulated-confirmation learning, replay pass, observations."""
    run_start = time.perf_counter()
    async with SqliteStorage(db_url) as storage:
        access = _IndexedMemoryAccess(storage)
        store = _IndexedMemoryStore(storage)
        orchestrator = Orchestrator(memory_store=store)
        await access.reload()
        await store.reload()

        pass1_records = await run_pass(entries, orchestrator)
        pass1 = pass_stats(pass1_records)
        eligible = pass1["l2_eligible"]
        pass1["l2_hit"] = 0
        pass1["l2_miss"] = max(eligible - pass1["degraded"], 0)
        pass1["hit_rate"] = 0.0
        pass1["medium_to_high"] = 0

        plan = build_learn_plan(pass1_records)
        learn = await run_learn(plan, access, storage)

        # 查询侧索引从 DB 重载学习结果；「HIGH 也查记忆」估算用刚学完、
        # 尚未被 pass2 hit 污染的信任分状态。
        await store.reload()
        estimate = await high_lookup_estimate(pass1_records, store)

        pass2_records = await run_pass(entries, orchestrator)
        pass2 = pass_stats(pass2_records)
        # The L2 eligibility denominator is the pass1 MEDIUM population;
        # successful fusion intentionally shrinks pass2 remaining MEDIUM count.
        pass2["l2_eligible"] = eligible
        pass2["l2_hit"] = pass2["routes"][ROUTE_MEMORY]
        pass2["l2_miss"] = max(eligible - pass2["l2_hit"] - pass2["degraded"], 0)
        pass2["hit_rate"] = round(pass2["l2_hit"] / eligible, 4) if eligible else 0.0
        pass2["medium_to_high"] = sum(
            1
            for r in pass2_records
            if r.route == ROUTE_MEMORY
            and r.result is not None
            and r.result.level is Confidence.HIGH
        )
        pass2["l2_miss_samples"] = [
            {
                "line": r.entry.line_number,
                "kind": r.entry.kind,
                "name": r.entry.name,
                "title": r.result.title if r.result is not None else None,
            }
            for r in pass2_records
            if r.route == ROUTE_L3
            and r.error is None
            and r.result is not None
            and r.result.level is Confidence.MEDIUM
        ][:_MAX_FAILED_SAMPLES]
        pass2["fused_samples"] = [
            {
                "line": r.entry.line_number,
                "name": r.entry.name,
                "title": r.result.title if r.result is not None else None,
                "key_level": (r.result.evidence.get("key_level") if r.result is not None else None),
            }
            for r in pass2_records
            if r.route == ROUTE_MEMORY
            and r.result is not None
            and r.result.level is Confidence.HIGH
        ][:_MAX_FAILED_SAMPLES]

        memory = memory_stats(await storage.list(ParseMemory))
        sweep = await MemoryGovernance(storage).sweep_status()

        observations = {
            "returned_none": returned_none_observation(pass1_records, pass2_records),
            "season_residue": season_residue_observation(pass1_records, pass2_records),
            "high_also_lookup_estimate": estimate,
        }

    return {
        "total": len(entries),
        "pass1": pass1,
        "learn": learn,
        "pass2": pass2,
        "memory": memory,
        "governance_sweep": {
            "demoted_to_pending": sweep.demoted_to_pending,
            "deprecated": sweep.deprecated,
            "unchanged": sweep.unchanged,
        },
        "observations": observations,
        "elapsed_s": round(time.perf_counter() - run_start, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="对真实下载快照做 L2 两遍验证（冷启动/学习/复测），输出统计 JSON"
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="快照文件路径（默认读取 AUTOANIME_L2_SNAPSHOT 或仓库上一级 notes 样本）",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="sqlite 文件路径（默认内存库，跑完即弃）",
    )
    args = parser.parse_args(argv)
    args.snapshot = args.snapshot if args.snapshot is not None else default_snapshot_path()
    if not args.snapshot.is_file():
        print(f"snapshot not found: {args.snapshot}", file=sys.stderr)
        return 2
    db_url = (
        f"sqlite+aiosqlite:///{args.db}"
        if args.db is not None
        else "sqlite+aiosqlite:///:memory:"
    )
    entries = list(parse_snapshot_lines(args.snapshot.read_text(encoding="utf-8-sig")))
    report = asyncio.run(run_two_pass(entries, db_url))
    report["snapshot"] = args.snapshot.name
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    total_failed = (
        report["pass1"]["failed"]
        + report["pass2"]["failed"]
        + report["learn"]["learn_failed"]
    )
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
