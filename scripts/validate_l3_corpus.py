"""T6: L3（LLM fallback）真实快照离线结构级验证（fake transport，零网络）。

对全量快照（约 2606 条真实下载记录）跑完整管线 L1 → L2（冷启动空记忆）
→ L3（fake transport）→ arbiter，统计 L3 介入效果与仲裁行为。全程离线、
可复现：不发生任何真实 LLM 网络调用，快照文件不进仓库（``--snapshot``
可覆盖路径，定位约定与 ``validate_l1_corpus.py`` / ``validate_l2_corpus.py``
一致：``[F]``/``[D]`` 前缀、``#`` 注释行）。

fake transport 策略（确定性规则，只从快照行自身可静态提取的字段构造响应，
模拟一个「能读懂发布名」的 LLM 的保守行为）：

1. 从 prompt 的 ``Release name: <raw>`` 行取回原始发布名（fake 不看 L1
   提示与上下文，只用发布名本身）；
2. title：首个含 CJK 的方括号内容优先（剥掉尾部「第N季」），否则剥离
   组标签、尾部 ``[...]`` 标签与扩展名后，截断到 season/episode/分辨率/
   年份等噪声标记，剩余 token 拼接；任何一步失败兜底为清洗后的原始名，
   保证 title 永远非空；
3. season：``S01`` / ``Season 2`` / ``4th Season`` / ``第2季`` / 中文数字
   ``第二季``；
4. episode：``S01E03`` / ``E03`` / ``EP01`` / `` - 41``；
5. fansub：首个 ASCII 方括号组（如 ``[BeanSub&FZSD&LoliHouse]``），否则
   尾部 ``[Group]`` 标签，否则尾部 ``-Group``（如 ``-UBWEB``）；
6. segment：剧场版/Movie 关键词 → ``movie``；Complete/全集/合集 →
   ``season_pack``；有 season 无 episode → ``season_pack``；其余 →
   ``episode``；
7. 取不到的字段一律 null。因此 fake 响应恒通过 L3 严格 schema（真实验
   证中 transport 失败/非法响应路径由单元测试覆盖，不在本脚本重复）。

两遍设计：pass1 冷缓存全量（真实调用路径 + 写缓存）；pass2 复用同一
llm_cache 回放（验证 T2 缓存命中语义，不触发 transport）。记忆库保持
冷启动为空（L2 恒 miss，MEDIUM 全部进入 L3），不模拟确认学习。

统计口径（顶层键为 pass1 冷启动口径；``cache_hit`` 为两遍合计）：

- ``l3_entered``：L3 识别器 ``enhance`` 被调用的条数（= 非 archive 且未
  失败的条目全部经过 L3）；
- ``l3_fake_hit``：L3 产出草稿并进入仲裁（``l3_applied``）的条数；命中率
  = ``l3_fake_hit / l3_entered``；
- ``arbiter_accepted``：最终结果至少一个字段证据为 ``llm``（L3 建议被
  采纳进最终结果）；
- ``arbiter_rejected``：L3 有产出但最终结果无任何 ``llm`` 证据字段（建议
  全部被更高优先级证据否决，R1 只补不覆盖）；
- ``arbiter_upgraded``：audit 中 ``level_upgraded`` 行数（R4/R5 升档）；
- ``degraded``：管线标记降级的条数（L3 未接线/失败的降级）；
- ``cache_hit``：llm_cache ``get`` 命中次数（pass2 应约等于全量回放）。

真实 LLM 小样本实测不在本脚本范围（**由主会话在用户确认后另行执行**，
本脚本零网络）；输出 ``real_llm_candidates``：共 10 条、按行号排序，取
L1 真正吃力、LLM 最可能有介入价值的样本，优先级：

1. L1 结果为 ``None``（本轮快照仅 1 条）；
2. L1 结果为 LOW（本轮快照为 0 条）；
3. L1 为 MEDIUM 但 ``missing_fields`` 非空（字段残缺，L1 无法补齐）。

含原始文件名与行号，供主会话取用。

单条异常容错：记录 failed 继续跑；无快照环境单元测试自动 skip。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import (
    LlmCacheStore,
    LlmTransport,
    ParseContext,
    ParseResult,
    RawName,
)
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.lookup import StorageMemoryStore
from autoanime.memory.store import SqliteStorage, StorageLlmCacheStore
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l3 import (
    L3_EVIDENCE,
    L3_FIELDS,
    LlmCache,
)
from autoanime.pipeline.l3.arbiter import (
    AUDIT_FIELD_CONFLICT,
    AUDIT_LEVEL_UPGRADED,
    AUDIT_SEASON_DISAMBIGUATED,
)
from autoanime.pipeline.l3_llm import LlmFallbackRecognizer
from autoanime.pipeline.orchestrator import (
    ROUTE_ARCHIVE,
    ROUTE_L3,
    ROUTE_MEMORY,
    Orchestrator,
)

_ROOT = Path(__file__).resolve().parent.parent

_SNAPSHOT_RELATIVE_PATH = Path("notes") / "samples" / "z_downloads_snapshot.txt"

_MAX_FAILED_SAMPLES = 10

_MAX_LLM_CANDIDATES = 10

_FAKE_MODEL = "fake-offline-rules"

# ---------------------------------------------------------------------------
# 快照解析（与 validate_l2_corpus.py 同一约定）
# ---------------------------------------------------------------------------


def default_snapshot_path() -> Path:
    """Resolve the external snapshot without hard-coding a machine path."""
    from_env = os.getenv("AUTOANIME_L3_SNAPSHOT")
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
# fake LLM 响应构造（确定性规则，只依赖发布名本身）
# ---------------------------------------------------------------------------

_EXTENSION_RE = re.compile(
    r"\.(?:mkv|mp4|avi|rmvb|ts|iso|ass|srt|ssa|sup|flv|wmv|m2ts)$", re.IGNORECASE
)
_LEADING_BRACKET_RE = re.compile(r"^\[([^\]]*)\]\s*")
_TRAILING_BRACKET_RE = re.compile(r"\[([^\]]*)\]")
_CJK_RE = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_CN_SEASON_RE = re.compile(r"第\s*([零一二三四五六七八九十]+|\d{1,3})\s*季")
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_TAGISH_RE = re.compile(r"rip|raw|web|bd|dvd|hdtv|1080|720|2160", re.IGNORECASE)
_MOVIE_RE = re.compile(r"剧场版|劇場版|[Gg]ekijouban|\bMovie\b")
_COMPLETE_RE = re.compile(r"\bComplete\b|全集|合集|季包", re.IGNORECASE)
_CUT_RE = re.compile(
    r"(?ix)"
    r"\s+-\s+\d"
    r"|(?<![a-z0-9])[Ss]\d{1,2}(?:[\s._-]*[Ee][Pp]?\d{1,4})?(?![0-9])"
    r"|(?<![a-z0-9])[Ee][Pp]?\d{1,4}(?![0-9])"
    r"|\b(?:Season|Complete|WebRip|WEB-?DL|BDRip|BluRay|REMUX|H\.?26[45]|x26[45]"
    r"|HEVC|AAC|FLAC|DDP|10bit)\b"
    r"|\b1080[pi]?\b|\b720p\b|\b2160p\b"
    r"|\b(?:19|20)\d{2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)(?:[\s._-]*Season)?\b"
)
_SEASON_ASCII_RES = (
    re.compile(r"(?<![A-Za-z0-9])[Ss](\d{1,2})(?![0-9])"),
    re.compile(r"[Ss]eason\s*(\d{1,2})", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)[\s._-]*Season", re.IGNORECASE),
)
_EPISODE_ASCII_RES = (
    re.compile(r"[Ss]\d{1,2}[\s._-]*[Ee][Pp]?(\d{1,4})(?![0-9])"),
    re.compile(r"(?<![A-Za-z0-9])[Ee][Pp]?(\d{1,4})(?![0-9])"),
    re.compile(r"\s+-\s+(\d{1,4})(?![0-9])"),
    re.compile(r"\[(\d{1,4})\]"),
)
_PROMPT_NAME_RE = re.compile(r"^Release name: (.+)$", re.MULTILINE)


def _cn_numeral(text: str) -> int | None:
    """中文数字（一~九十九）转整数；不认识的返回 ``None``。"""
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        if (left and left not in _CN_DIGITS) or (right and right not in _CN_DIGITS):
            return None
        return tens * 10 + ones
    return _CN_DIGITS.get(text)


def _cn_season_value(text: str) -> int | None:
    """「第N季」括号内文本转季数：阿拉伯数字或中文数字。"""
    return int(text) if text.isdigit() else _cn_numeral(text)


def _fake_season(name: str) -> int | None:
    match = _CN_SEASON_RE.search(name)
    if match:
        return _cn_season_value(match.group(1))
    for pattern in _SEASON_ASCII_RES:
        if (match := pattern.search(name)):
            return int(match.group(1))
    return None


def _fake_episode(name: str) -> int | None:
    for pattern in _EPISODE_ASCII_RES:
        if (match := pattern.search(name)):
            return int(match.group(1))
    return None


def _fake_fansub(name: str) -> str | None:
    stripped = _EXTENSION_RE.sub("", name).strip()
    match = _LEADING_BRACKET_RE.match(stripped)
    if match:
        content = match.group(1).strip()
        rest = stripped[match.end() :]
        looks_like_group = (
            not _TAGISH_RE.search(content)
            and len(content) <= 40
            and (
                not _CJK_RE.search(content)
                or rest.startswith("[")  # 「[组名][标题]」形态：首括号是字幕组
            )
        )
        if content and looks_like_group:
            return content
    for candidate in _TRAILING_BRACKET_RE.findall(stripped):
        content = candidate.strip()
        if (
            stripped.rstrip().endswith(f"[{candidate}]")
            and content            and not _CJK_RE.search(content)
            and not _TAGISH_RE.search(content)
            and len(content) <= 20
        ):
            return content
    match = re.search(r"-([A-Za-z][A-Za-z0-9]{1,15})$", stripped)
    if match:
        return match.group(1)
    return None


def _fake_title(name: str) -> str:
    stripped = _EXTENSION_RE.sub("", name).strip()
    match = _LEADING_BRACKET_RE.match(stripped)
    if match and _CJK_RE.search(match.group(1)):
        rest = stripped[match.end() :]
        second = _LEADING_BRACKET_RE.match(rest)
        if second and _CJK_RE.search(second.group(1)):
            # 「[组名][标题]」形态：第一个方括号是字幕组，标题取第二个。
            return _CN_SEASON_RE.split(second.group(1))[0].strip()
        return _CN_SEASON_RE.split(match.group(1))[0].strip()
    if match:
        stripped = stripped[match.end() :].strip()
    body = _TRAILING_BRACKET_RE.sub(" ", stripped)
    if (cut := _CUT_RE.search(body)):
        body = body[: cut.start()]
    title = re.sub(r"[._]+", " ", body).strip(" -")
    if not title:
        title = re.sub(r"[._]+", " ", stripped).strip(" -")
    return title or "Unknown Release"


def _fake_segment(name: str, season: int | None, episode: int | None) -> Segment:
    if _MOVIE_RE.search(name):
        return Segment.MOVIE
    if _COMPLETE_RE.search(name):
        return Segment.SEASON_PACK
    if season is not None and episode is None:
        return Segment.SEASON_PACK
    return Segment.EPISODE


def build_fake_response(release_name: str) -> str:
    """按确定性规则从发布名构造一份通过严格 schema 的 LLM 响应 JSON。"""
    season = _fake_season(release_name)
    episode = _fake_episode(release_name)
    payload = {
        "title": _fake_title(release_name),
        "season": season,
        "episode": episode,
        "segment": _fake_segment(release_name, season, episode).value,
        "fansub": _fake_fansub(release_name),
    }
    return json.dumps(payload, ensure_ascii=False)


def raw_name_from_prompt(prompt: str) -> str:
    """从首轮 prompt 中取回发布名（fake transport 不读 L1 提示与上下文）。"""
    match = _PROMPT_NAME_RE.search(prompt)
    return match.group(1).strip() if match else ""


@dataclass
class FakeRuleTransport:
    """``LlmTransport`` fake：按确定性规则构造 schema 合法响应，零网络。"""

    calls: list[str] = field(default_factory=list)

    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str:
        self.calls.append(prompt)
        return build_fake_response(raw_name_from_prompt(prompt))


class _CountingLlmRecognizer(LlmFallbackRecognizer):
    """计数 ``enhance`` 调用次数（= 进入 L3 的条数），其余行为不变。"""

    def __init__(self) -> None:
        super().__init__(enabled=True, model=_FAKE_MODEL)
        self.enhance_calls = 0

    async def enhance(
        self,
        raw: RawName,
        result: ParseResult | None,
        context: ParseContext | None,
        transport: LlmTransport,
        cache_store: LlmCacheStore,
        *,
        operation_id: str | None = None,
    ) -> ParseResult | None:
        self.enhance_calls += 1
        return await super().enhance(
            raw, result, context, transport, cache_store, operation_id=operation_id
        )


class _CountingLlmCacheStore(StorageLlmCacheStore):
    """统计 ``get`` 命中次数的 llm_cache 适配器（T2 DB 实现原样复用）。"""

    def __init__(self, storage: SqliteStorage) -> None:
        super().__init__(storage)
        self.hits = 0

    async def get(self, pattern_hash: str) -> LlmCache | None:
        cached = await super().get(pattern_hash)
        if cached is not None:
            self.hits += 1
        return cached


# ---------------------------------------------------------------------------
# 跑批与统计
# ---------------------------------------------------------------------------


@dataclass
class PassRecord:
    """One entry's outcome in one pass."""

    entry: SnapshotEntry
    raw: RawName
    result: ParseResult | None
    route: str
    l2_applied: bool
    degraded: bool
    l3_applied: bool
    llm_evidence_fields: tuple[str, ...]
    upgraded: bool
    conflict_rows: int
    disambiguated_rows: int
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
                    l3_applied=False,
                    llm_evidence_fields=(),
                    upgraded=False,
                    conflict_rows=0,
                    disambiguated_rows=0,
                    duration_s=time.perf_counter() - start,
                    error=type(exc).__name__,
                )
            )
            continue
        result = outcome.result
        records.append(
            PassRecord(
                entry=entry,
                raw=raw,
                result=result,
                route=outcome.route,
                l2_applied=outcome.l2_applied,
                degraded=outcome.degraded,
                l3_applied=outcome.l3_applied,
                llm_evidence_fields=tuple(
                    name
                    for name in L3_FIELDS
                    if result is not None and result.evidence.get(name) == L3_EVIDENCE
                ),
                upgraded=any(a.action == AUDIT_LEVEL_UPGRADED for a in outcome.audit),
                conflict_rows=sum(1 for a in outcome.audit if a.action == AUDIT_FIELD_CONFLICT),
                disambiguated_rows=sum(
                    1 for a in outcome.audit if a.action == AUDIT_SEASON_DISAMBIGUATED
                ),
                duration_s=time.perf_counter() - start,
            )
        )
    return records


def _duration_stats(durations: Sequence[float]) -> dict[str, float]:
    if not durations:
        return {"avg_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "total_s": 0.0}
    ordered = sorted(durations)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        "avg_ms": round(sum(durations) / len(durations) * 1000, 3),
        "p95_ms": round(p95 * 1000, 3),
        "max_ms": round(max(durations) * 1000, 3),
        "total_s": round(sum(durations), 3),
    }


def pass_stats(
    records: Sequence[PassRecord],
    *,
    l3_entered: int,
    transport_calls: int,
    cache_hits: int,
) -> dict[str, Any]:
    """Deterministic statistics block for one pass."""
    ok_records = [r for r in records if r.error is None]
    parsed_results = [r.result for r in ok_records if r.result is not None]
    route_counts: Counter[str] = Counter(r.route for r in ok_records)
    level_counts: Counter[str] = Counter(r.level.value for r in parsed_results)
    failed = [r for r in records if r.error is not None]
    l3_fake_hit = sum(1 for r in ok_records if r.l3_applied)
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
            "archive": route_counts.get(ROUTE_ARCHIVE, 0),
            "memory": route_counts.get(ROUTE_MEMORY, 0),
            "l3": route_counts.get(ROUTE_L3, 0),
        },
        "levels": {
            key: level_counts.get(key, 0) for key in ("high", "medium", "low")
        },
        "degraded": sum(1 for r in records if r.degraded),
        "l3_entered": l3_entered,
        "l3_fake_hit": l3_fake_hit,
        "l3_fake_hit_rate": round(l3_fake_hit / l3_entered, 4) if l3_entered else 0.0,
        "arbiter_accepted": sum(1 for r in ok_records if r.llm_evidence_fields),
        "arbiter_rejected": sum(
            1 for r in ok_records if r.l3_applied and not r.llm_evidence_fields
        ),
        "arbiter_upgraded": sum(1 for r in records if r.upgraded),
        "arbiter_field_conflicts": sum(r.conflict_rows for r in records),
        "arbiter_season_disambiguated": sum(r.disambiguated_rows for r in records),
        "transport_calls": transport_calls,
        "cache_hit": cache_hits,
        "duration_ms": _duration_stats([r.duration_s for r in ok_records]),
    }


async def collect_llm_candidates(
    entries: Sequence[SnapshotEntry], limit: int = _MAX_LLM_CANDIDATES
) -> list[dict[str, Any]]:
    """真实 LLM 实测候选：L1 吃力的样本，按行号排序，共 ``limit`` 条。

    优先级：L1 ``None`` > L1 LOW > L1 MEDIUM 且 ``missing_fields`` 非空
    （字段残缺，LLM 补齐价值最高）。单独跑一遍 L1（LocalRecognizer），
    单条异常按「无结果」处理继续跑。
    """
    recognizer = LocalRecognizer()
    buckets: dict[str, list[dict[str, Any]]] = {"none": [], "low": [], "medium_gap": []}
    for entry in entries:
        raw = to_raw_name(entry)
        try:
            result = await recognizer.parse(raw)
        except Exception:  # noqa: BLE001 -- 单条容错
            result = None
        info = {"line": entry.line_number, "kind": entry.kind, "name": entry.name}
        if result is None:
            buckets["none"].append({**info, "l1": "none"})
        elif result.level is Confidence.LOW:
            buckets["low"].append({**info, "l1": "low"})
        elif result.missing_fields:
            buckets["medium_gap"].append(
                {
                    **info,
                    "l1": "medium",
                    "missing_fields": ",".join(result.missing_fields),
                }
            )
    selected: list[dict[str, Any]] = []
    for key in ("none", "low", "medium_gap"):
        ordered = sorted(buckets[key], key=lambda item: item["line"])
        selected.extend(ordered)
        if len(selected) >= limit:
            break
    return selected[:limit]


async def run_validation(
    entries: Sequence[SnapshotEntry], db_url: str = "sqlite+aiosqlite:///:memory:"
) -> dict[str, Any]:
    """Cold pass + warm-cache replay over the full pipeline (offline fake LLM)."""
    run_start = time.perf_counter()
    async with SqliteStorage(db_url) as storage:
        governance = MemoryGovernance(storage)
        transport = FakeRuleTransport()
        recognizer = _CountingLlmRecognizer()
        cache_store = _CountingLlmCacheStore(storage)
        orchestrator = Orchestrator(
            memory_store=StorageMemoryStore(storage),
            l2_enabled=True,
            l3_enabled=True,
            l3_recognizer=recognizer,
            llm_transport=transport,
            llm_cache_store=cache_store,
            audit_sink=governance,
        )

        pass1_records = await run_pass(entries, orchestrator)
        pass1 = pass_stats(
            pass1_records,
            l3_entered=recognizer.enhance_calls,
            transport_calls=len(transport.calls),
            cache_hits=cache_store.hits,
        )

        # pass2：同一 llm_cache 回放（不触发 transport），验证 T2 缓存命中语义。
        transport.calls.clear()
        pass2_records = await run_pass(entries, orchestrator)
        pass2 = pass_stats(
            pass2_records,
            l3_entered=recognizer.enhance_calls - pass1["l3_entered"],
            transport_calls=len(transport.calls),
            cache_hits=cache_store.hits - pass1["cache_hit"],
        )

        candidates = await collect_llm_candidates(entries)

    failed = pass1["failed"] + pass2["failed"]
    return {
        "total": len(entries),
        "l3_entered": pass1["l3_entered"],
        "l3_fake_hit": pass1["l3_fake_hit"],
        "l3_fake_hit_rate": pass1["l3_fake_hit_rate"],
        "arbiter_accepted": pass1["arbiter_accepted"],
        "arbiter_rejected": pass1["arbiter_rejected"],
        "arbiter_upgraded": pass1["arbiter_upgraded"],
        "arbiter_field_conflicts": pass1["arbiter_field_conflicts"],
        "arbiter_season_disambiguated": pass1["arbiter_season_disambiguated"],
        "degraded": pass1["degraded"],
        "cache_hit": pass1["cache_hit"] + pass2["cache_hit"],
        "elapsed_ms": round((time.perf_counter() - run_start) * 1000, 1),
        "routes": pass1["routes"],
        "levels": pass1["levels"],
        "failed": failed,
        "failed_samples": pass1["failed_samples"] or pass2["failed_samples"],
        "duration_ms": pass1["duration_ms"],
        "passes": {"pass1": pass1, "pass2": pass2},
        "real_llm_candidates": candidates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="对真实下载快照做 L3 离线验证（fake transport，零网络），输出统计 JSON"
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="快照文件路径（默认读取 AUTOANIME_L3_SNAPSHOT 或仓库上一级 notes 样本）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="统计 JSON 落盘路径（默认只打印 stdout）",
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
    report = asyncio.run(run_validation(entries, db_url))
    report["snapshot"] = args.snapshot.name
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
