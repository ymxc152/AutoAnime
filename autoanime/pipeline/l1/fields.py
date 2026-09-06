"""Field extraction on top of anchor spans: season, episode, fansub, segment."""

from __future__ import annotations

import re

from autoanime.core.enums import Segment
from autoanime.pipeline.l1.anchors import (
    AnchorKind,
    AnchorSpan,
    find_anchors,
    find_anchors_of_kind,
)
from autoanime.pipeline.l1.normalize import normalize_whitespace, strip_extension

_DIGITS_RE = re.compile(r"\d{1,4}")
_MOVIE_MARKERS_RE = re.compile(r"劇場版|剧场版|電影|电影|Movie", re.IGNORECASE)
# A plausible group name contains at least one letter, digit or CJK char
# (CJK range 一-鿿); bare punctuation residue ("]", "-", "…") never qualifies.
_FANSUB_CHAR_RE = re.compile(r"[A-Za-z0-9一-鿿]")
_NON_FANSUB_RE = re.compile(
    r"(?:4320|2160|1440|1080|720|480|360)p"
    r"|x26[45]|H\.?26[45]|HEVC|AVC|Hi10P?|\d{1,2}bit"
    r"|WEB-?DL|WEBRip|Blu-?ray|BDRip|DVDRip|Remux|HDTV"
    r"|DD[P+]?\.?\d|E-?AC-?3|Atmos|TrueHD"
    # 版本噪声（"TV版&无修版"等）是发布版本标记，不是字幕组名（F01 契约）。
    r"|TV版|无修版?|未删减|无删节|修正版|高清修复|熟肉|精校"
    r"|B-Global|Baha|Bahamut|friDay|AT-X|BiliBili|Crunchyroll|FunTV|AI-Raws"
    r"|ASS|ASSx2|AAC|FLAC|MP3|MP4|MKV|AV1|VSR|10bit|8bit|简体|繁体|简日|繁日|内嵌|内封|无字幕|chinese|japanese"
    # 字幕语言组合标签的开放形态（简／繁、简&繁、简日双语…）——用模式覆盖，
    # 不逐字枚举（F01 契约：这类标签不是字幕组名）。
    r"|[简簡].{0,2}[繁日體]|双语|雙語|中字|外挂|外掛",
    re.IGNORECASE,
)


def _first_number(spans: list[AnchorSpan]) -> int | None:
    for span in spans:
        if match := _DIGITS_RE.search(span.text):
            return int(match.group(0))
    return None


def extract_season(text: str) -> int | None:
    """First explicit season marker (S01 / Season 2 / 第2季)."""
    return _first_number(find_anchors_of_kind(text, AnchorKind.SEASON))


def extract_episode(text: str) -> int | None:
    """First explicit episode marker (E01 / EP 01 / 第01话 / "- 41")."""
    return _first_number(find_anchors_of_kind(text, AnchorKind.EPISODE))


def extract_episode_numbers(text: str) -> list[int]:
    """All episode markers in positional order (multi-episode and batch files)."""
    return [
        int(match.group(0))
        for span in find_anchors_of_kind(text, AnchorKind.EPISODE)
        if (match := _DIGITS_RE.search(span.text)) is not None
    ]


def is_likely_fansub(token: str) -> bool:
    """Heuristic: a bracket/trailing token that is not a technical marker."""
    if not token or len(token) > 40:
        return False
    if token.isdigit():
        return False
    # A group name carries at least one letter/digit/CJK char; a residue of
    # bare punctuation (e.g. a stray "]" after a bracket-internal anchor) is
    # never a fansub.
    if not _FANSUB_CHAR_RE.search(token):
        return False
    return _NON_FANSUB_RE.search(token) is None


def extract_fansub(text: str) -> str | None:
    """Fansub group from the first plausible bracket token, else trailing -Group."""
    for span in find_anchors_of_kind(text, AnchorKind.BRACKET):
        inner = span.text.lstrip("[【").rstrip("]】")
        if is_likely_fansub(inner):
            return inner
    base = normalize_whitespace(strip_extension(text))
    structural = [span for span in find_anchors(base) if span.kind is not AnchorKind.BRACKET]
    tail = base[max(span.end for span in structural) :].lstrip() if structural else base
    tail = tail.lstrip("-").strip()
    if tail and is_likely_fansub(tail):
        return tail
    return None


def detect_segment(
    text: str, *, season: int | None = None, episode: int | None = None
) -> Segment | None:
    """Segment implied by movie markers or the season/episode combination.

    Returns None when the structure alone cannot tell (the dialect decides).
    """
    if _MOVIE_MARKERS_RE.search(text):
        return Segment.MOVIE
    if episode is not None:
        return Segment.EPISODE
    if season is not None:
        return Segment.SEASON_PACK
    return None
