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
_NON_FANSUB_RE = re.compile(
    r"(?:4320|2160|1440|1080|720|480|360)p"
    r"|x26[45]|H\.?26[45]|HEVC|AVC|Hi10P?|\d{1,2}bit"
    r"|WEB-?DL|WEBRip|Blu-?ray|BDRip|DVDRip|Remux|HDTV"
    r"|B-Global|Baha|Bahamut|friDay|AT-X|BiliBili|Crunchyroll|FunTV|AI-Raws"
    r"|ASS|ASSx2|AAC|FLAC|MP3|MP4|MKV|AV1|VSR|10bit|8bit|简体|繁体|简日|繁日|内嵌|内封|无字幕|chinese|japanese",
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
