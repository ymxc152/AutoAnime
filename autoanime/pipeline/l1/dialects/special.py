"""Dialect F: edge-case releases -- version noise, movies, odd season packs.

Covers releases whose difficulty is noise or missing structure rather than
exotic markers:

- TV版/无修版 version brackets and 简／繁 subtitle-language brackets that look
  like fansub groups but are not (they must never surface as ``fansub``).
- Theatrical releases with no episode/season markers, detected via explicit
  movie markers or a standalone year; the segment is always MOVIE and never
  forced into an episode shape.
- Season packs whose folder carries a source tag (CR) and an additional group
  bracket: the filename group wins and the disagreement downgrades the result
  once, per the unified merge rules.
- Jammed word-internal spaces (FateGrand) mark the title reconstruction as
  dubious, which caps the result at MEDIUM.

The entry point is a pure function; nothing here is registered in the
external-capability registry (L1 is a fixed pipeline stage).
"""

from __future__ import annotations

import re
from dataclasses import replace

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.anchors import anchor_free_chunks
from autoanime.pipeline.l1.anitopy_adapter import parse_with_anitopy
from autoanime.pipeline.l1.context import (
    SOURCE_FOLDER,
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
from autoanime.pipeline.l1.normalize import (
    normalize_whitespace,
    separators_to_spaces,
    strip_extension,
)

_PURE_DIGIT_STEM_RE = re.compile(r"^\d{1,4}$")
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_TRAILING_YEAR_RE = re.compile(r"\s+(?:19|20)\d{2}$")
_JAMMED_WORD_RE = re.compile(r"[a-z][A-Z]")
_VERSION_NOISE_RE = re.compile(r"TV版|無修|无修|無刪|无删|未刪|未删|簡|繁|简|生肉|熟肉|內嵌|内嵌|內封|内封")
_SUBTITLE_FORMAT_RE = re.compile(r"^(?:SRT|ASS|SSA|SUP|PGS)x?\d*$", re.IGNORECASE)


def _as_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _plausible_fansub(token: object) -> str | None:
    """A fansub candidate that is not version/subtitle-language noise."""
    if not isinstance(token, str):
        return None
    token = normalize_whitespace(token)
    if not token or len(token) > 40:
        return None
    if _VERSION_NOISE_RE.search(token) or _SUBTITLE_FORMAT_RE.fullmatch(token):
        return None
    if not is_likely_fansub(token):
        return None
    return token


def _clean_title(raw_title: str) -> str | None:
    title = _TRAILING_YEAR_RE.sub("", normalize_whitespace(raw_title)).strip(" -_")
    return title or None


def _title_from_chunks(text: str) -> str | None:
    """First non-digit anchor-free chunk, with separators rebuilt as spaces."""
    for chunk in anchor_free_chunks(text):
        candidate = _clean_title(separators_to_spaces(chunk))
        if candidate and not candidate.isdigit():
            return candidate
    return None


def _side_draft(text: str, source: str) -> L1Draft | None:
    """Parse one side (filename or folder) into a draft.

    The side draft is graded HIGH when it yields a title, i.e. its own
    extraction succeeded; overall completeness is graded after the merge via
    ``finalized``. A pure-digit stem is dialect G territory and returns None.
    """
    stripped = strip_extension(text)
    if not stripped or _PURE_DIGIT_STEM_RE.fullmatch(stripped):
        return None

    title = _title_from_chunks(stripped)
    if title is None:
        return None

    parsed = parse_with_anitopy(stripped)
    season = _as_int(parsed.get("anime_season"))
    if season is None:
        season = extract_season(stripped)
    episode = _as_int(parsed.get("episode_number"))
    if episode is None:
        episode = extract_episode(stripped)
    fansub = _plausible_fansub(parsed.get("release_group"))
    if fansub is None:
        fansub = _plausible_fansub(extract_fansub(stripped))

    segment = detect_segment(stripped, season=season, episode=episode)
    if segment is None and _YEAR_RE.search(stripped):
        segment = Segment.MOVIE

    evidence = {
        "title": source,
        "season": source if season is not None else SOURCE_NONE,
        "episode": source if episode is not None else SOURCE_NONE,
        "segment": source if segment is not None else SOURCE_NONE,
        "fansub": source if fansub is not None else SOURCE_NONE,
    }
    return L1Draft(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub=fansub,
        level=Confidence.HIGH,
        evidence=evidence,
    )


def parse_special(raw: RawName, context: ParseContext | None = None) -> ParseResult | None:
    """Parse a dialect-F release name; None when L1 cannot give a result.

    Merge rules: the filename draft owns every field it provides, the folder
    draft fills gaps, and any disagreement downgrades the merged level once.
    The folder segment is dropped when the filename already determined one, so
    a season-pack folder around episode files is not counted as a conflict.
    """
    if _PURE_DIGIT_STEM_RE.fullmatch(strip_extension(raw.name)):
        return None

    name_draft = _side_draft(raw.name, SOURCE_NAME)
    folder_draft = _side_draft(raw.folder, SOURCE_FOLDER) if raw.folder else None

    if name_draft is None:
        if folder_draft is None:
            return None
        merged = folder_draft
    else:
        if folder_draft is not None and name_draft.segment is not None:
            folder_draft = replace(folder_draft, segment=None)
        merged = merge_folder_draft(name_draft, folder_draft)

    merged = merged.finalized()
    if merged.segment is None:
        return None
    if _JAMMED_WORD_RE.search(merged.title):
        merged = merged.downgraded()
    return apply_release_progress(merged, context).to_parse_result()
