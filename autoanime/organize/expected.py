"""订阅快路径对齐校验 + 错配恢复 A/B/C 决策（E4，Plan §6.1 / D13 / D14）。

D13：expected 权威载体是 ``release_record(episode_id, torrent_hash)``——
RSS 命中即落库，下载完成扫描逐文件附 expected 传入管线；对齐一致走
HIGH 快路径（跳过 L2 查找与 API 匹配，audit 记 ``subscribed_fast_path``）；
手动导入 expected=None，两路共用同一套解析代码。

对齐三出口（纯函数，免 LLM）：
- ``fast_path``：剧名命中期望番 + 季集对上 → 直接归档；
- ``episode_variant``：同番但集数不同/双集/SP → 确定性规则处理；
- ``conflict``：指向另一部番/季不符/解析失败但可判矛盾 → 文件名优先且
  降档进仲裁，该 release 标疑似错标。

错配恢复三分支（整理时发现 expected 与文件不符：先隔离 + rejected 落库，
再诊断）：
- **A 改挂**：解析出有效归属、同番、目标集存在 → 改挂 release 到该集，
  零重下；
- **B 人工**：解析失败/陌生番/证据矛盾 → 隔离 + pending_queue（附证据链）；
- **C 回补**：同番但文件不可救（目标集不存在/已被占）→ 隔离待清理、
  expected 集回 MISSING 立即回补；单集回补预算默认 2 次，超限转人工，
  并把该 torrent_hash 拉黑（防错标源霸榜死循环）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from autoanime.core.interfaces import ParseResult

AlignmentVerdict = Literal["fast_path", "episode_variant", "conflict", "unparsed"]
MismatchBranch = Literal["A_reattach", "B_manual", "C_backfill", "C_budget_exhausted"]


def normalize_title(text: str) -> str:
    """宽松归一（NFKC + casefold + 去分隔符），用于剧名命中判定。"""
    collapsed = re.sub(r"[\s\-_.·～~「」『』()（）\[\]【】]+", "", text)
    return collapsed.casefold()


@dataclass(frozen=True)
class ExpectedContext:
    """随文件传入管线的订阅期望（来自 release_record → episode → series）。"""

    series_id: int
    season_number: int
    episode_number: int
    title_cn: str | None = None
    title_jp: str | None = None
    title_romaji: str | None = None
    fansub_pref: str | None = None
    torrent_hash: str | None = None
    release_record_id: int | None = None

    def titles(self) -> tuple[str, ...]:
        return tuple(t for t in (self.title_cn, self.title_jp, self.title_romaji) if t)


@dataclass(frozen=True)
class Alignment:
    """L1 结果与 expected 的对齐结论（明细随行，供 audit/UI）。"""

    verdict: AlignmentVerdict
    detail: str
    parsed_episode: int | None = None
    parsed_season: int | None = None


def title_matches(parsed_title: str, expected_titles: tuple[str, ...]) -> bool:
    """剧名命中：归一后相等，或一方完整包含另一方（防「第二季」后缀差异）。

    v1 纪律：不引入模糊阈值——包含判定够用且可解释；误判由对齐后的
    集数真实性闸门与错配恢复兜底。
    """
    normalized_parsed = normalize_title(parsed_title)
    if not normalized_parsed:
        return False
    for candidate in expected_titles:
        normalized_candidate = normalize_title(candidate)
        if not normalized_candidate:
            continue
        if normalized_parsed == normalized_candidate:
            return True
        if normalized_candidate in normalized_parsed or normalized_parsed in normalized_candidate:
            return True
    return False


def align_with_expected(
    parse: ParseResult | None, expected: ExpectedContext
) -> Alignment:
    """对齐校验（纯函数）：expected 是证据之一，文件名解析结果优先。"""
    if parse is None:
        return Alignment("unparsed", "pipeline returned no parse result")
    season = parse.season if parse.season is not None else expected.season_number
    if not title_matches(parse.title, expected.titles()):
        return Alignment(
            "conflict",
            f"title '{parse.title}' does not match expected series",
            parsed_episode=parse.episode,
            parsed_season=parse.season,
        )
    if season != expected.season_number:
        return Alignment(
            "conflict",
            f"parsed season {season} != expected {expected.season_number}",
            parsed_episode=parse.episode,
            parsed_season=parse.season,
        )
    if parse.episode is None:
        # 同番无集数（SEASON_PACK/剧场版）：Mikan 订阅不支持（Plan §6 第 1 项
        # 实操坑），确定性转 episode_variant 交调用方按段处理，不硬套集数。
        return Alignment(
            "episode_variant",
            f"same series but segment {parse.segment.value} without episode",
            parsed_episode=None,
            parsed_season=season,
        )
    if parse.episode == expected.episode_number:
        return Alignment(
            "fast_path",
            "title, season and episode all match expected",
            parsed_episode=parse.episode,
            parsed_season=season,
        )
    return Alignment(
        "episode_variant",
        f"same series but parsed episode {parse.episode} != expected {expected.episode_number}",
        parsed_episode=parse.episode,
        parsed_season=season,
    )


def align_rss_entry(
    parse: ParseResult | None,
    *,
    expected_titles: tuple[str, ...],
    season_number: int,
) -> Alignment:
    """RSS 条目对齐（季级）：订阅源按番/季绑定，条目可命中季内任意集。

    与 :func:`align_with_expected`（文件级，organize 时用 expected episode）
    的分工：RSS 阶段只判「是不是这番这一季的条目」，集号交给候选匹配。
    ``fast_path`` = 同番同季且带集号（候选）；``episode_variant`` = 同番同季
    但无集数（SEASON_PACK/MOVIE——Mikan 订阅不支持，确定性拒绝）；
    ``conflict`` = 异番/异季（错标源，不下载）。
    """
    if parse is None:
        return Alignment("unparsed", "pipeline returned no parse result")
    season = parse.season if parse.season is not None else season_number
    if not title_matches(parse.title, expected_titles):
        return Alignment(
            "conflict",
            f"title '{parse.title}' does not match expected series",
            parsed_episode=parse.episode,
            parsed_season=parse.season,
        )
    if season != season_number:
        return Alignment(
            "conflict",
            f"parsed season {season} != expected {season_number}",
            parsed_episode=parse.episode,
            parsed_season=parse.season,
        )
    if parse.episode is None:
        return Alignment(
            "episode_variant",
            f"same series but segment {parse.segment.value} without episode",
            parsed_episode=None,
            parsed_season=season,
        )
    return Alignment(
        "fast_path",
        "same series and season with an episode number",
        parsed_episode=parse.episode,
        parsed_season=season,
    )


# ---------------------------------------------------------------------------
# 错配恢复 A/B/C（D14；纯决策，落库/文件操作由调用方执行）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MismatchEvidence:
    """错配诊断输入（整理时 expected 与文件不符后的证据快照）。"""

    parse_valid: bool
    title_match: bool
    target_episode_id: int | None
    target_episode_state: str | None
    backfill_used: int
    budget: int = 2
    evidence_conflict: bool = False


@dataclass(frozen=True)
class MismatchDecision:
    """A/B/C 决策（字段即调用方的执行清单）。"""

    branch: MismatchBranch
    quarantine: bool
    reattach_episode_id: int | None
    backfill: bool
    blacklist_hash: bool
    to_pending_queue: bool
    detail: str


def decide_mismatch(evidence: MismatchEvidence) -> MismatchDecision:
    """错配恢复三分支（决策表参数化单测钉死，先预算后分支顺序见下）。

    判定顺序：
    1. 解析有效 + 同番 + 目标集存在且仍 MISSING → A（改挂，零重下）；
    2. 解析失败/陌生番/证据矛盾 → B（人工，隔离 + pending）；
    3. 同番但目标集不可用（不存在/已 ORGANIZED 等被占用）→ C（回补重下）；
       预算用尽 → 转人工 + torrent_hash 拉黑（防死循环烧流量）。

    改挂目标 v1 只认 MISSING：目标已在下载/已归档说明有另一份内容在途或
    就位，改挂会覆盖别人的文件——确定性转 C 分支隔离重下。
    """
    if evidence.parse_valid and evidence.title_match and not evidence.evidence_conflict:
        if (
            evidence.target_episode_id is not None
            and evidence.target_episode_state == "missing"
        ):
            return MismatchDecision(
                branch="A_reattach",
                quarantine=False,
                reattach_episode_id=evidence.target_episode_id,
                backfill=False,
                blacklist_hash=False,
                to_pending_queue=False,
                detail="parsed attribution valid; reattach release to correct episode",
            )
        used = evidence.backfill_used
        if used < evidence.budget:
            return MismatchDecision(
                branch="C_backfill",
                quarantine=True,
                reattach_episode_id=None,
                backfill=True,
                blacklist_hash=True,
                to_pending_queue=False,
                detail=(
                    f"file unusable for expected episode; backfill "
                    f"{used + 1}/{evidence.budget} and blacklist this hash"
                ),
            )
        return MismatchDecision(
            branch="C_budget_exhausted",
            quarantine=True,
            reattach_episode_id=None,
            backfill=False,
            blacklist_hash=True,
            to_pending_queue=True,
            detail=(
                f"backfill budget exhausted ({used}/{evidence.budget}); "
                "escalate to manual and blacklist hash"
            ),
        )
    return MismatchDecision(
        branch="B_manual",
        quarantine=True,
        reattach_episode_id=None,
        backfill=False,
        blacklist_hash=False,
        to_pending_queue=True,
        detail="parse failed / unknown series / conflicting evidence; needs manual triage",
    )
