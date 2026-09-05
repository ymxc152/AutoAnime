"""Dialect C: pure bracket-flow release names.

Dialect shape: ``[Group][Title][EP][1920x1080][AVC_AAC][CHT]`` with no
separators between brackets. Recognized traits:

- the fansub group sits in the first bracket (positional rule);
- no season marker: dialect C names are episode-only, so ``season`` stays
  absent and the draft keeps its missing-season MEDIUM grade;
- ``1920x1080`` style resolutions (no ``p`` suffix) and ``AVC_AAC`` style
  codec/language tokens are technical brackets, never fansub or title;
- the episode is the first purely-numeric bracket.

anitopy is only used as a title fallback and cross-check; every structural
field comes from the bracket positions.
"""

from __future__ import annotations

import re
from dataclasses import replace

from autoanime.core.enums import Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.anchors import AnchorKind, find_anchors_of_kind
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
from autoanime.pipeline.l1.normalize import normalize_name, normalize_whitespace

# Technical bracket bodies: resolutions (1920x1080), episode numbers and
# ranges, codec/audio/subtitle/language tokens. Fansub names such as
# ``64bitsub`` deliberately do NOT full-match (trailing letters).
_TECH_INNER_RE = re.compile(
    r"(?:"
    r"\d{3,4}\s*[xX×]\s*\d{3,4}"
    r"|\d{1,4}(?:\s*[-~]\s*\d{1,4})?"
    r"|[xXhH]\.?26[45]"
    r"|HEVC|AVC|AV1|AAC|FLAC|MP3|MP4|ASS|SSA|SRT|Hi10P?"
    r"|\d{1,2}bit"
    r"|CHS|CHT|GBR|BIG5|繁|简"
    r")(?:_[A-Za-z0-9]+)*",
    re.IGNORECASE,
)


def parse(raw: RawName, context: ParseContext | None = None) -> ParseResult | None:
    """Parse one dialect-C release name; None when L1 cannot help."""
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
    inners = [
        span.text[1:-1].strip()
        for span in find_anchors_of_kind(base, AnchorKind.BRACKET)
    ]
    if not inners:
        return None  # no bracket flow: not dialect-C shaped

    fansub = inners[0] if not _is_technical(inners[0]) else None
    title = next(
        (inner for inner in inners[1:] if not _is_technical(inner)),
        None,
    )
    episode = next((int(inner) for inner in inners if inner.isdigit()), None)

    anitopy = parse_with_anitopy(text)
    if title is None:
        title = normalize_whitespace(anitopy.get("anime_title", ""))
    if not title:
        return None

    level = base_level(title=title, season=None, episode=episode, segment=Segment.EPISODE)
    if _anitopy_conflict(anitopy, episode=episode):
        level = downgrade(level)

    return L1Draft(
        title=title,
        season=None,
        episode=episode,
        segment=Segment.EPISODE,
        fansub=fansub,
        level=level,
        missing_fields=missing_fields_for(
            title=title, season=None, episode=episode, segment=Segment.EPISODE
        ),
        evidence={
            "title": SOURCE_NAME,
            "season": SOURCE_NONE,
            "episode": SOURCE_NAME if episode is not None else SOURCE_NONE,
            "segment": SOURCE_NAME,
            "fansub": SOURCE_NAME if fansub is not None else SOURCE_NONE,
        },
    )


def _is_technical(inner: str) -> bool:
    return _TECH_INNER_RE.fullmatch(inner) is not None


def _anitopy_conflict(anitopy: dict[str, str], *, episode: int | None) -> bool:
    raw = anitopy.get("episode_number")
    if raw is None or episode is None or not raw.isdigit():
        return False
    return int(raw) != episode
