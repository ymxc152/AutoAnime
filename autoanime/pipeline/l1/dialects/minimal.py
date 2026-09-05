"""Dialect G: minimal numeric filenames that depend on the folder context.

A file literally named ``01.mkv`` carries exactly one bit of information: its
episode number. Title, season, and fansub can only come from the folder, and
the evidence must say so. Without a folder (or when the folder yields no title
chunk) L1 cannot give a meaningful result and returns None. A trailing
``-Group`` on the folder and an ``S02``-style season marker are honored.

The entry point is a pure function; nothing here is registered in the
external-capability registry (L1 is a fixed pipeline stage).
"""

from __future__ import annotations

import re

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.anchors import anchor_free_chunks
from autoanime.pipeline.l1.anitopy_adapter import parse_with_anitopy
from autoanime.pipeline.l1.context import (
    apply_release_progress,
    merge_folder_draft,
)
from autoanime.pipeline.l1.draft import L1Draft
from autoanime.pipeline.l1.fields import extract_fansub, extract_season, is_likely_fansub
from autoanime.pipeline.l1.normalize import separators_to_spaces, strip_extension

_DIGIT_STEM_RE = re.compile(r"^\d{1,4}$")
_ANIME_PREFIX_RE = re.compile(r"^Anime[\s._-]+", re.IGNORECASE)
_JAMMED_WORD_RE = re.compile(r"[a-z][A-Z]")


def _as_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _title_from_folder(folder: str) -> str | None:
    """First non-digit anchor-free chunk; drops a leading ``Anime`` prefix."""
    for chunk in anchor_free_chunks(folder):
        candidate = separators_to_spaces(chunk)
        if not candidate or candidate.isdigit():
            continue
        return _ANIME_PREFIX_RE.sub("", candidate, count=1) or candidate
    return None


def _folder_fansub(parsed: dict[str, str], folder: str) -> str | None:
    for candidate in (parsed.get("release_group"), extract_fansub(folder)):
        if candidate and is_likely_fansub(candidate):
            return candidate
    return None


def _folder_draft(folder: str) -> L1Draft | None:
    """Folder-derived draft; the segment stays None (the filename decides)."""
    title = _title_from_folder(folder)
    if title is None:
        return None
    parsed = parse_with_anitopy(folder)
    season = _as_int(parsed.get("anime_season"))
    if season is None:
        season = extract_season(folder)
    return L1Draft(
        title=title,
        season=season,
        fansub=_folder_fansub(parsed, folder),
        segment=None,
        level=Confidence.HIGH,
    )


def parse_minimal(raw: RawName, context: ParseContext | None = None) -> ParseResult | None:
    """Parse a minimal numeric filename; None when the context cannot help.

    The filename contributes only the episode number; title, season, and
    fansub are filled from the folder and their evidence records ``folder``.
    A jammed word-internal space in the reconstructed title caps the result
    at MEDIUM (dubious title reconstruction).
    """
    stem = strip_extension(raw.name)
    if not _DIGIT_STEM_RE.fullmatch(stem):
        return None
    if not raw.folder:
        return None
    folder_draft = _folder_draft(raw.folder)
    if folder_draft is None:
        return None

    name_draft = L1Draft(
        title="",
        episode=int(stem),
        segment=Segment.EPISODE,
        level=Confidence.HIGH,
    )
    merged = merge_folder_draft(name_draft, folder_draft).finalized()
    if _JAMMED_WORD_RE.search(merged.title):
        merged = merged.downgraded()
    return apply_release_progress(merged, context).to_parse_result()
