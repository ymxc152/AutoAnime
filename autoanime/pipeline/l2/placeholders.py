"""L2 placeholder abstraction: title shape templates and backfill.

A *title shape* is the casefolded, separator-folded form of a title in which
the digit runs inside season/episode markers are replaced by the
``{season}`` / ``{ep}`` placeholders, so one learned entry can match every
episode (and season marker spelling) of a series. The learning side builds
the shape from the confirmed title; the query side backfills a learned shape
into a concrete title.

Pure functions only: no I/O, no module-level mutable state.
"""

from __future__ import annotations

import re

from autoanime.pipeline.l1.anchors import AnchorKind, find_anchors
from autoanime.pipeline.l1.normalize import (
    normalize_whitespace,
    separators_to_spaces,
    strip_extension,
)

SEASON_PLACEHOLDER = "{season}"
EPISODE_PLACEHOLDER = "{ep}"

_DIGITS_RE = re.compile(r"\d+")

# Season and episode anchors start on distinct tokens (S/Season/第..季 vs
# E/EP/第..话/" - N"), so their spans never overlap and right-to-left
# replacement keeps earlier offsets valid.


def build_title_shape(title: str) -> str:
    """Normalized title template with season/episode digits replaced.

    NFC/whitespace folding, extension stripping and dot/underscore folding
    run first; then every digit run inside a SEASON/EPISODE anchor becomes
    the matching placeholder; casefold runs last. Titles without structural
    markers only change by normalization.
    """
    base = separators_to_spaces(normalize_whitespace(strip_extension(title)))
    shaped = base
    for span in reversed(find_anchors(base)):
        if span.kind is AnchorKind.SEASON:
            token = SEASON_PLACEHOLDER
        elif span.kind is AnchorKind.EPISODE:
            token = EPISODE_PLACEHOLDER
        else:
            continue
        digits = _DIGITS_RE.search(base[span.start : span.end])
        if digits is None:
            continue
        start = span.start + digits.start()
        end = span.start + digits.end()
        shaped = shaped[:start] + token + shaped[start + (end - start) :]
    return shaped.casefold()


def backfill_title(
    shape: str, *, season: int | None = None, episode: int | None = None
) -> str | None:
    """Fill placeholders with concrete values.

    Returns ``None`` when a placeholder is present but its value is missing;
    a shape without placeholders passes through unchanged. The result is in
    shape (casefolded) form; the display title lives in the stored result.
    """
    result = shape
    if SEASON_PLACEHOLDER in result:
        if season is None:
            return None
        result = result.replace(SEASON_PLACEHOLDER, str(season))
    if EPISODE_PLACEHOLDER in result:
        if episode is None:
            return None
        result = result.replace(EPISODE_PLACEHOLDER, str(episode))
    return result
