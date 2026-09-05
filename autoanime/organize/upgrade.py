"""洗版引擎的确定性决策层（E4）：评分公式 + 升级判定（纯函数）。

评分公式（ARCHITECTURE §3 原文，逐项实现；参考 Sonarr Custom Formats 的
「候选评分制 + 阈值升级判定」思想，只学判定逻辑不抄代码）::

    score = 4 × 分辨率分(1080p=4, 720p=2, ≤576=1)
          + 3 × 来源分(B-Global/Baha/ATX WEB-DL=3, Web Rip=2, HDTV=1)
          + 2 × 编码分(HEVC=2, AVC=1)
          + 2 × 字幕组偏好(用户指定=2, 默认=1)   ← Series.fansub_pref
          + min(2, log10(seeders+1))             # 做种健康度,封顶防单点

D15 降级契约：RSS 场景 seeders/size 未知 → 0 分参与、**不剔除**。
公式原文未定义 2160p 档位 → 按未知 0 分处理（4K 支持进 backlog，报告已记）。

升级判定：新候选 score ≥ 现有 score + upgrade_threshold（默认 2，可配）
且 upgraded_count < 上限（默认 2）。全部决策可解释（reason 字段），
洗版触发/评分是确定性代码（铁律 4，不进 AI 边界）。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

#: 升级判定原因（决策表单测钉死）。
UpgradeReason = Literal["allowed", "threshold_not_met", "upgrade_limit_reached"]


@dataclass(frozen=True)
class QualityTokens:
    """从发布标题扫出的技术词（识别不出 = None → 0 分参与，不剔除）。"""

    resolution: str | None = None
    source: str | None = None
    codec: str | None = None


_RES_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 顺序即优先级：先匹配高精度档位，防止 1080 被 108 抢走。
    ("2160p", re.compile(r"(?<!\d)2160p?(?!\d)", re.IGNORECASE)),
    ("1080p", re.compile(r"(?<!\d)1080[pi](?!\d)", re.IGNORECASE)),
    ("720p", re.compile(r"(?<!\d)720p(?!\d)", re.IGNORECASE)),
    ("576p", re.compile(r"(?<!\d)576p(?!\d)", re.IGNORECASE)),
    ("480p", re.compile(r"(?<!\d)480p(?!\d)", re.IGNORECASE)),
)

_SOURCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("b-global", re.compile(r"b[-_. ]?global", re.IGNORECASE)),
    ("baha", re.compile(r"b[-_. ]?aha|Bahamut", re.IGNORECASE)),
    ("atx", re.compile(r"ATX", re.IGNORECASE)),
    ("web-dl", re.compile(r"web[._ -]?dl", re.IGNORECASE)),
    ("webrip", re.compile(r"web[._ -]?rip", re.IGNORECASE)),
    ("hdtv", re.compile(r"hdtv", re.IGNORECASE)),
)

_CODEC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hevc", re.compile(r"hevc|x265|h\.?265", re.IGNORECASE)),
    ("avc", re.compile(r"avc|x264|h\.?264", re.IGNORECASE)),
)


def parse_quality_tokens(title: str) -> QualityTokens:
    """扫发布标题取技术词；多词命中取公式优先档（高分档优先）。"""
    resolution = _first_hit(_RES_PATTERNS, title)
    source = _first_hit(_SOURCE_PATTERNS, title)
    codec = _first_hit(_CODEC_PATTERNS, title)
    return QualityTokens(resolution=resolution, source=source, codec=codec)


def _first_hit(
    patterns: tuple[tuple[str, re.Pattern[str]], ...], title: str
) -> str | None:
    for name, pattern in patterns:
        if pattern.search(title):
            return name
    return None


def resolution_points(resolution: str | None) -> float:
    """分辨率分：1080p=4, 720p=2, ≤576=1；其余（含 2160p/未知）= 0。"""
    if resolution == "1080p":
        return 4.0
    if resolution == "720p":
        return 2.0
    if resolution in ("576p", "480p"):
        return 1.0
    return 0.0


def source_points(source: str | None) -> float:
    """来源分：B-Global/Baha/ATX/WEB-DL=3, WebRip=2, HDTV=1；未知 = 0。"""
    if source in ("b-global", "baha", "atx", "web-dl"):
        return 3.0
    if source == "webrip":
        return 2.0
    if source == "hdtv":
        return 1.0
    return 0.0


def codec_points(codec: str | None) -> float:
    """编码分：HEVC=2, AVC=1；未知 = 0。"""
    if codec == "hevc":
        return 2.0
    if codec == "avc":
        return 1.0
    return 0.0


def fansub_points(fansub: str | None, fansub_pref: str | None) -> float:
    """字幕组偏好：用户指定命中 = 2；其余（含未配置偏好/组未知）= 默认 1。"""
    if fansub_pref and fansub and fansub.strip().casefold() == fansub_pref.strip().casefold():
        return 2.0
    return 1.0


def seeders_points(seeders: int | None) -> float:
    """做种健康度：min(2, log10(seeders+1))；未知（RSS 无该字段）= 0。"""
    if seeders is None or seeders < 0:
        return 0.0
    return min(2.0, math.log10(seeders + 1))


def score_release(
    *,
    resolution: str | None,
    source: str | None,
    codec: str | None,
    fansub: str | None,
    fansub_pref: str | None,
    seeders: int | None,
) -> float:
    """ARCHITECTURE §3 评分公式的逐项实现（纯函数，满分 4+3+2+2+2=13）。

    各桶分值即公式括号内的取值（1080p=4 / B-Global=3 / HEVC=2 / 偏好命中=2 /
    做种封顶 2），与拍板口径「分辨率4/来源3/编码2/字幕组2/做种封顶2」一致。
    """
    return (
        resolution_points(resolution)
        + source_points(source)
        + codec_points(codec)
        + fansub_points(fansub, fansub_pref)
        + seeders_points(seeders)
    )


def score_from_title(
    title: str,
    *,
    fansub: str | None,
    fansub_pref: str | None,
    seeders: int | None,
) -> float:
    """RSS 场景的便捷入口：技术词从标题扫，字幕组来自解析结果。"""
    tokens = parse_quality_tokens(title)
    return score_release(
        resolution=tokens.resolution,
        source=tokens.source,
        codec=tokens.codec,
        fansub=fansub,
        fansub_pref=fansub_pref,
        seeders=seeders,
    )


@dataclass(frozen=True)
class UpgradeDecision:
    """洗版触发判定（可解释决策，审计/UI 共用）。"""

    allowed: bool
    reason: UpgradeReason
    candidate_score: float
    current_score: float
    threshold: float
    upgraded_count: int


def decide_upgrade(
    *,
    candidate_score: float,
    current_score: float,
    upgraded_count: int,
    threshold: float = 2.0,
    max_upgrades: int = 2,
) -> UpgradeDecision:
    """触发 = 新分 ≥ 现分 + 阈值，且未到单集洗版上限（先查上限）。"""
    if upgraded_count >= max_upgrades:
        return UpgradeDecision(
            False, "upgrade_limit_reached", candidate_score, current_score, threshold, upgraded_count
        )
    if candidate_score < current_score + threshold:
        return UpgradeDecision(
            False, "threshold_not_met", candidate_score, current_score, threshold, upgraded_count
        )
    return UpgradeDecision(
        True, "allowed", candidate_score, current_score, threshold, upgraded_count
    )
