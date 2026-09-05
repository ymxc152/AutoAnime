"""Dialect E recognizer: ANi-style traditional-Chinese episode names.

Typical shape (fixed ``[ANi]`` prefix, title, dash/EP episode, technical
brackets)::

    [ANi] 碧藍航線 微速前進！2！！ - 02 [1080P][Baha][WEB-DL][AAC AVC][CHT].mp4
    [ANi] 地獄模式 ～...～ 2nd Season - 14 [1080P][Baha][WEB-DL][AAC AVC][CHT].mp4

Rules implemented here, on top of the shared L1 primitives:

- Gate: the name must start with the ``[ANi]`` bracket; anything else is not
  this dialect.
- Fansub is always ``ANi`` taken from the name; the source tag ``Baha`` and
  the technical brackets never leak into it, and ``ParseContext.fansub_pref``
  never rewrites it.
- Season markers: anchor seasons (``S2`` / ``第2季``), Chinese numerals
  (``第二季``), ``2nd Season`` ordinal words, and ``2！！`` exclamation
  suffixes -- the season number hiding inside the title.
- Episode: shared anchors (``- 02`` dash form and ``EP02``/``E02`` prefixes).
- Title: the bracket-free middle part with season and episode spans removed.
- Grading: a Chinese title with no folder context caps the level at MEDIUM;
  season markers that disagree inside the name are a conflict (LOW); a
  folder-provided season fills a gap (evidence ``folder``) or, on
  disagreement, the name wins with a one-step downgrade.
"""

from __future__ import annotations

import re

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1 import (
    SOURCE_FOLDER,
    SOURCE_NAME,
    SOURCE_NONE,
    AnchorKind,
    AnchorSpan,
    L1Draft,
    apply_release_progress,
    base_level,
    downgrade,
    extract_episode,
    find_anchors_of_kind,
    merge_levels,
    normalize_name,
    normalize_whitespace,
    strip_extension,
)
from autoanime.pipeline.l1.dialects.cjk import has_cjk
from autoanime.pipeline.l1.dialects.cjk import season_spans as cjk_season_spans

_FANSUB = "ANi"
_ANI_PREFIX_RE = re.compile(r"^[\[【]\s*ANi\s*[\]】]", re.IGNORECASE)
_BRACKET_START_RE = re.compile(r"[\[【]")
_ORDINAL_SEASON_RE = re.compile(r"(?P<num>\d{1,2})\s*(?:st|nd|rd|th)\s*(?:Season|季)", re.IGNORECASE)
_EXCL_SEASON_RE = re.compile(r"(?P<num>\d{1,2})\s*[！!]{2,}")


def _season_matches(text: str) -> list[tuple[int, int, int]]:
    """Every season marker as ``(start, end, value)`` in positional order.

    Adds the two ANi-specific forms (``2nd Season`` ordinals and ``2！！``
    exclamation suffixes) to the shared anchor + Chinese-numeral set.
    """
    spans = list(cjk_season_spans(text))
    for pattern in (_ORDINAL_SEASON_RE, _EXCL_SEASON_RE):
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end(), int(match.group("num"))))
    return sorted(spans)


def _build_title(
    mid: str,
    episode_spans: list[AnchorSpan],
    season_matches: list[tuple[int, int, int]],
) -> str:
    """The bracket-free middle part with every season/episode span masked out."""
    masked = list(mid)
    for start, end, _ in season_matches:
        for index in range(start, min(end, len(masked))):
            masked[index] = "\x00"
    for span in episode_spans:
        for index in range(span.start, min(span.end, len(masked))):
            masked[index] = "\x00"
    return normalize_whitespace("".join(masked).replace("\x00", " "))


def parse(raw: RawName, context: ParseContext | None = None) -> ParseResult | None:
    """Parse one ANi-style name; None when the shape is not this dialect."""
    name_text = normalize_name(raw.name)
    prefix = _ANI_PREFIX_RE.match(name_text)
    if prefix is None:
        return None

    rest = name_text[prefix.end() :]
    next_bracket = _BRACKET_START_RE.search(rest)
    mid = normalize_whitespace(rest[: next_bracket.start()] if next_bracket else rest)

    episode_spans = find_anchors_of_kind(mid, AnchorKind.EPISODE)
    episode = extract_episode(mid)
    season_matches = _season_matches(mid)
    season_values = {value for _, _, value in season_matches}
    season = season_matches[0][2] if season_matches else None
    title = _build_title(mid, episode_spans, season_matches)
    if not title:
        return None

    season_src = SOURCE_NAME if season is not None else SOURCE_NONE
    folder_conflict = False
    if raw.folder:
        folder_text = normalize_whitespace(strip_extension(raw.folder))
        folder_matches = _season_matches(folder_text)
        folder_season = folder_matches[0][2] if folder_matches else None
        if season is None and folder_season is not None:
            season, season_src = folder_season, SOURCE_FOLDER
        elif season is not None and folder_season is not None and folder_season != season:
            folder_conflict = True

    if episode is not None:
        segment = Segment.EPISODE
    elif season is not None:
        segment = Segment.SEASON_PACK
    else:
        return None

    level = base_level(title=title, season=season, episode=episode, segment=segment)
    if raw.folder is None and has_cjk(title):
        level = merge_levels(level, Confidence.MEDIUM)
    if len(season_values) > 1:
        level = Confidence.LOW
    if folder_conflict:
        level = downgrade(level)

    draft = L1Draft(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub=_FANSUB,
        level=level,
        evidence={
            "title": SOURCE_NAME,
            "season": season_src,
            "episode": SOURCE_NAME,
            "segment": SOURCE_NAME,
            "fansub": SOURCE_NAME,
        },
    )
    return apply_release_progress(draft, context).finalized().to_parse_result()
