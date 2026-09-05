"""Dialect B: bracket-prefixed release names with ``- episode`` markers.

Dialect shape: ``[Fansub] Title - EP [tech]``. Recognized traits:

- multi-fansub co-signing in the first bracket (``BeanSub&FZSD&LoliHouse``);
- season hidden in the title as an ordinal phrase (``4th Season``) as well as
  explicit ``S2`` markers;
- double episode ``14(86)``: the first number is the season-relative episode
  and is the one reported; the parenthesised absolute number is ignored;
- batch ranges ``[03-06]``: the range start is reported as the episode
  candidate and the draft keeps its missing-season MEDIUM grade.

anitopy is only used as a title fallback and cross-check; every structural
field comes from the shared anchor/field rules.
"""

from __future__ import annotations

import re
from dataclasses import replace

from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.anchors import AnchorKind, AnchorSpan, find_anchors, find_anchors_of_kind
from autoanime.pipeline.l1.anitopy_adapter import parse_with_anitopy
from autoanime.pipeline.l1.confidence import base_level, downgrade, missing_fields_for
from autoanime.pipeline.l1.context import (
    SOURCE_CONTEXT,
    SOURCE_NAME,
    SOURCE_NONE,
    apply_release_progress,
    merge_folder_draft,
)
from autoanime.pipeline.l1.draft import L1Draft
from autoanime.pipeline.l1.fields import (
    detect_segment,
    extract_episode,
    extract_season,
    is_likely_fansub,
)
from autoanime.pipeline.l1.normalize import normalize_name, normalize_whitespace

_BATCH_RANGE_RE = re.compile(r"\d{1,4}\s*[-~]\s*\d{1,4}")
_ORDINAL_SEASON_RE = re.compile(r"\b(?P<season>\d{1,2})(?:st|nd|rd|th)\s+Season\b", re.IGNORECASE)
_SEASON_MARKER_RE = re.compile(r"\b(?:S\d{1,2}|Season\s*\d{1,2})\b", re.IGNORECASE)
_DOUBLE_EPISODE_RE = re.compile(r"\b(?P<main>\d{1,4})\((?P<alt>\d{1,4})\)")


def parse(raw: RawName, context: ParseContext | None = None) -> ParseResult | None:
    """Parse one dialect-B release name; None when L1 cannot help."""
    name_draft = _parse_text(raw.name)
    if name_draft is None:
        return None
    folder_draft = _parse_text(raw.folder) if raw.folder and raw.folder != raw.name else None
    draft = merge_folder_draft(name_draft, folder_draft)
    draft = apply_release_progress(draft, context)
    if context is not None and context.release_progress is not None:
        draft = replace(draft, evidence={**draft.evidence, "release_progress": SOURCE_CONTEXT})
    if not draft.title:
        return None
    return draft.finalized().to_parse_result()


def _parse_text(text: str) -> L1Draft | None:
    base = normalize_name(text)
    if not base:
        return None
    brackets = find_anchors_of_kind(base, AnchorKind.BRACKET)
    if not brackets:
        return None  # no bracket structure: not dialect-B shaped
    unbracketed = _without_brackets(base, brackets)
    if not unbracketed:
        return None

    fansub = _fansub(brackets)
    season = extract_season(unbracketed)
    if season is None:
        season = _ordinal_season(unbracketed)
    episode = _episode(unbracketed, brackets)
    if season is None and episode is None:
        return None

    title = _title(unbracketed)
    anitopy = parse_with_anitopy(text)
    if not title:
        title = normalize_whitespace(anitopy.get("anime_title", ""))
    if not title:
        return None

    segment = detect_segment(unbracketed, season=season, episode=episode)
    level = base_level(title=title, season=season, episode=episode, segment=segment)
    if _anitopy_conflict(anitopy, season=season, episode=episode):
        level = downgrade(level)

    return L1Draft(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub=fansub,
        level=level,
        missing_fields=missing_fields_for(
            title=title, season=season, episode=episode, segment=segment
        ),
        evidence={
            "title": SOURCE_NAME,
            "season": SOURCE_NAME if season is not None else SOURCE_NONE,
            "episode": SOURCE_NAME if episode is not None else SOURCE_NONE,
            "segment": SOURCE_NAME if segment is not None else SOURCE_NONE,
            "fansub": SOURCE_NAME if fansub is not None else SOURCE_NONE,
        },
    )


def _without_brackets(base: str, brackets: list[AnchorSpan]) -> str:
    masked = list(base)
    for span in brackets:
        for index in range(span.start, min(span.end, len(masked))):
            masked[index] = " "
    return normalize_whitespace("".join(masked))


def _fansub(brackets: list[AnchorSpan]) -> str | None:
    for span in brackets:
        inner = span.text[1:-1].strip()
        if is_likely_fansub(inner):
            return inner
        return None  # the first bracket is the fansub slot in this dialect
    return None


def _ordinal_season(unbracketed: str) -> int | None:
    if match := _ORDINAL_SEASON_RE.search(unbracketed):
        return int(match.group("season"))
    return None


def _episode(unbracketed: str, brackets: list[AnchorSpan]) -> int | None:
    for span in brackets:
        if _BATCH_RANGE_RE.fullmatch(span.text[1:-1].strip()):
            start = span.text[1:-1].strip().split("-")[0].strip()
            return int(start)
    episode = extract_episode(unbracketed)
    if match := _DOUBLE_EPISODE_RE.search(unbracketed):
        # "14(86)": first number is the season-relative episode, the
        # parenthesised absolute number is deliberately not reported.
        return int(match.group("main"))
    return episode


def _title(unbracketed: str) -> str:
    spans = find_anchors(unbracketed)
    cuts = [
        span.start
        for span in spans
        if span.kind in (AnchorKind.EPISODE, AnchorKind.SEASON)
    ]
    region = unbracketed[: min(cuts)] if cuts else unbracketed
    region = _ORDINAL_SEASON_RE.sub(" ", region)
    region = _SEASON_MARKER_RE.sub(" ", region)
    return normalize_whitespace(region.strip(" -"))


def _anitopy_conflict(
    anitopy: dict[str, str], *, season: int | None, episode: int | None
) -> bool:
    for key, value in (("anime_season", season), ("episode_number", episode)):
        raw = anitopy.get(key)
        if raw is None or value is None or not raw.isdigit():
            continue
        if int(raw) != value:
            return True
    return False
