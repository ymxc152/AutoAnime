"""Dialect A: dot-separated release names (MWeb season packs and episodes).

Dialect shape: ``Title.S02E01.1080p.Source.WEB-DL.AAC2.0.H.264-Group`` with
dots as the universal separator. Recognized traits:

- MWeb whole-season packs (no episode marker -> season pack);
- Baha / friDay / LINETV source stations (LINETV is dialect-A specific and
  therefore extended beyond the shared anchor source list);
- word-internal dots (``BanG.Dream``) and all-caps titles make the title
  reconstruction uncertain, so such drafts are downgraded one level;
- anitopy season/episode values are cross-checked against anchor extraction;
  a disagreement is a field conflict and drops the draft to LOW.

The title is a parse candidate, not final metadata. anitopy is only used as a
title fallback and as a cross-check; every structural field comes from the
shared anchor/field rules.
"""

from __future__ import annotations

import re
from dataclasses import replace

from autoanime.core.enums import Confidence
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.anchors import AnchorKind, AnchorSpan, find_anchors
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
    extract_fansub,
    extract_season,
    is_likely_fansub,
)
from autoanime.pipeline.l1.normalize import normalize_name, separators_to_spaces

# Source stations specific to dialect A; the shared anchor list already covers
# Baha / friDay / B-Global.
_EXTRA_SOURCE_RE = re.compile(r"(?<![A-Za-z])(?:LINETV|LiTV)(?![A-Za-z])", re.IGNORECASE)

# A token ending in a lowercase->uppercase transition (``BanG``) or a bare
# single letter marks a word-internal dot, unlike ordinary CamelCase words
# (``AzurLane``) which merely use dots as separators.
_WORD_INTERNAL_RE = re.compile(r"[a-z][A-Z]$")


def parse(raw: RawName, context: ParseContext | None = None) -> ParseResult | None:
    """Parse one dialect-A release name; None when L1 cannot help."""
    name_draft = _parse_text(raw.name)
    if name_draft is None:
        return None
    folder_draft = _parse_text(raw.folder) if raw.folder and raw.folder != raw.name else None
    draft = merge_folder_draft(name_draft, folder_draft)
    draft = apply_release_progress(draft, context)
    if context is not None and context.release_progress is not None:
        draft = replace(draft, evidence={**draft.evidence, "release_progress": SOURCE_CONTEXT})
    if not draft.title or draft.segment is None:
        # No segment landmark in name or folder (e.g. pure-bracket or batch
        # names): not a meaningful dialect-A result, hand back to the other
        # dialects instead of violating the ParseResult precondition.
        return None
    return draft.finalized().to_parse_result()


def _parse_text(text: str) -> L1Draft | None:
    base = normalize_name(text)
    if not base:
        return None
    spans = _structural_spans(base)
    if not spans:
        return None  # no structural landmark at all: not dialect-A shaped

    season = extract_season(base)
    episode = extract_episode(base)
    title = _title_from(base, spans)

    anitopy = parse_with_anitopy(text)
    if not title:
        title = separators_to_spaces(anitopy.get("anime_title", ""))
    if not title:
        return None

    word_internal, all_upper = _title_ambiguity(base, spans)
    fansub = _fansub_from(base, spans)
    segment = detect_segment(base, season=season, episode=episode)

    level = base_level(title=title, season=season, episode=episode, segment=segment)
    if word_internal or all_upper:
        level = downgrade(level)
    if _anitopy_conflict(anitopy, season=season, episode=episode):
        level = Confidence.LOW

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


def _structural_spans(base: str) -> list[AnchorSpan]:
    spans = [span for span in find_anchors(base) if span.kind is not AnchorKind.BRACKET]
    spans.extend(
        AnchorSpan(AnchorKind.SOURCE, match.start(), match.end(), match.group(0))
        for match in _EXTRA_SOURCE_RE.finditer(base)
    )
    return sorted(spans, key=lambda span: (span.start, span.end))


def _title_from(base: str, spans: list[AnchorSpan]) -> str:
    region = base[: spans[0].start] if spans else base
    return separators_to_spaces(region.strip(" .-_"))


def _title_ambiguity(base: str, spans: list[AnchorSpan]) -> tuple[bool, bool]:
    region = base[: spans[0].start] if spans else base
    tokens = [token for token in region.split(".") if token]
    word_internal = any(
        _WORD_INTERNAL_RE.search(token) is not None or (len(token) == 1 and token.isalpha())
        for token in tokens
    )
    alpha = [token for token in tokens if token.isalpha()]
    all_upper = len(alpha) >= 2 and all(token.isupper() for token in alpha)
    return word_internal, all_upper


def _fansub_from(base: str, spans: list[AnchorSpan]) -> str | None:
    tail = base[max(span.end for span in spans) :].lstrip(" -") if spans else ""
    # A structural anchor may sit inside a bracket group ("[H264 8bit 1080P]"):
    # the tail then starts with bracket residue, which is not a fansub shape.
    stripped = tail.strip("[]【】") if tail else ""
    if stripped and is_likely_fansub(stripped):
        return stripped
    return extract_fansub(base)


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
