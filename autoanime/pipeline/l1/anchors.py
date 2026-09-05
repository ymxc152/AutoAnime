"""Anchor detection: locate structural markers inside a release name.

Anchors are the fixed landmarks of a release name -- season/episode markers,
resolution, source tokens, and bracketed groups. Everything left over after
removing the anchor spans is a title-candidate chunk. Dialect recognizers
combine these primitives with their own ordering rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from autoanime.pipeline.l1.normalize import normalize_whitespace, strip_extension


class AnchorKind(StrEnum):
    SEASON = "season"
    EPISODE = "episode"
    RESOLUTION = "resolution"
    CODEC = "codec"
    SOURCE = "source"
    BRACKET = "bracket"


@dataclass(frozen=True)
class AnchorSpan:
    kind: AnchorKind
    start: int
    end: int
    text: str


_SEASON_RE = re.compile(
    r"(?<![A-Za-z])(?:S(?P<latin>\d{1,2})|Season\s*(?P<word>\d{1,2})|第\s*(?P<cjk>\d{1,2})\s*[季層])",
    re.IGNORECASE,
)
_EPISODE_RE = re.compile(
    r"(?<![A-Za-z])(?:E(?:P)?\s*\.?\s*(?P<latin>\d{1,4})|第\s*(?P<cjk>\d{1,4})\s*[话話集])"
    r"|(?P<suffix>\s-\s\d{1,4}(?![0-9]))",
    re.IGNORECASE,
)
_RESOLUTION_RE = re.compile(r"(?<!\d)(?:4320|2160|1440|1080|720|480|360)p(?!\d)", re.IGNORECASE)
_CODEC_RE = re.compile(r"x26[45]|H\.?26[45]|HEVC|AVC|Hi10P?|\d{1,2}bit", re.IGNORECASE)
_SOURCE_TOKENS: tuple[str, ...] = (
    "WEB-DL",
    "WEBRip",
    "WebRip",
    "BluRay",
    "Blu-ray",
    "BDRip",
    "DVDRip",
    "Remux",
    "HDTV",
    "B-Global",
    "Baha",
    "Bahamut",
    "friDay",
    "AT-X",
    "BiliBili",
    "Crunchyroll",
    "FunTV",
    "AI-Raws",
)
_SOURCE_RE = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(re.escape(token) for token in _SOURCE_TOKENS) + r")(?![A-Za-z])",
    re.IGNORECASE,
)
_BRACKET_RE = re.compile(r"[\[【](?P<inner>[^\]】]{0,80})[\]】]")


def find_anchors(text: str) -> list[AnchorSpan]:
    """Return every anchor span in the text, ordered by position."""
    normalized = normalize_whitespace(text)
    spans: list[AnchorSpan] = []

    for match in _SEASON_RE.finditer(normalized):
        spans.append(AnchorSpan(AnchorKind.SEASON, match.start(), match.end(), match.group(0)))
    for match in _EPISODE_RE.finditer(normalized):
        spans.append(AnchorSpan(AnchorKind.EPISODE, match.start(), match.end(), match.group(0)))
    for match in _RESOLUTION_RE.finditer(normalized):
        spans.append(AnchorSpan(AnchorKind.RESOLUTION, match.start(), match.end(), match.group(0)))
    for match in _CODEC_RE.finditer(normalized):
        spans.append(AnchorSpan(AnchorKind.CODEC, match.start(), match.end(), match.group(0)))
    for match in _SOURCE_RE.finditer(normalized):
        spans.append(AnchorSpan(AnchorKind.SOURCE, match.start(), match.end(), match.group(0)))
    for match in _BRACKET_RE.finditer(normalized):
        spans.append(AnchorSpan(AnchorKind.BRACKET, match.start(), match.end(), match.group(0)))

    return sorted(spans, key=lambda span: (span.start, span.end))


def find_anchors_of_kind(text: str, kind: AnchorKind) -> list[AnchorSpan]:
    return [span for span in find_anchors(text) if span.kind is kind]


def anchor_free_chunks(text: str, spans: list[AnchorSpan] | None = None) -> list[str]:
    """Split the text at anchor boundaries and return the leftover regions.

    The remaining regions are title candidates; separators such as
    word-internal dots are preserved so each dialect can apply its own
    reconstruction rules. Internal whitespace stays intact, so a bracket-flow
    title survives as one chunk. ``spans`` (when given) must be anchored to
    the same normalized text.
    """
    base = normalize_whitespace(strip_extension(text))
    effective = find_anchors(base) if spans is None else spans
    masked = list(base)
    for span in effective:
        for index in range(span.start, min(span.end, len(masked))):
            masked[index] = "\x00"
    remainder = "".join(masked)
    return [chunk for region in remainder.split("\x00") if (chunk := region.strip(" .-_"))]
