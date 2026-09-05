"""Context merging: filename vs folder precedence and ParseContext downgrades.

Merge rules from the PR3 contract:
- When filename and folder conflict, the filename wins and the merged draft is
  downgraded one level overall.
- When the filename lacks a field, the folder may fill it in.
- Evidence records whether each field came from "name", "folder", "context",
  or is absent ("none").
"""

from __future__ import annotations

from dataclasses import replace

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext
from autoanime.pipeline.l1.confidence import downgrade, merge_levels, missing_fields_for
from autoanime.pipeline.l1.draft import L1Draft

SOURCE_NAME = "name"
SOURCE_FOLDER = "folder"
SOURCE_CONTEXT = "context"
SOURCE_NONE = "none"

_MERGED_FIELDS: tuple[str, ...] = ("title", "season", "episode", "segment", "fansub")


def choose_prefer_name[T](
    name_value: T | None, folder_value: T | None
) -> tuple[T | None, str]:
    """Pick the filename value first; fall back to the folder value."""
    if name_value is not None:
        return name_value, SOURCE_NAME
    if folder_value is not None:
        return folder_value, SOURCE_FOLDER
    return None, SOURCE_NONE


def merge_folder_draft(name_draft: L1Draft, folder_draft: L1Draft | None) -> L1Draft:
    """Merge a filename-derived draft with an optional folder-derived draft.

    The filename draft owns every field it provides; the folder draft only
    fills gaps. Any disagreement on a both-sides-present field downgrades the
    merged level once overall.
    """
    if folder_draft is None:
        return name_draft

    merged_values: dict[str, object] = {}
    sources: dict[str, str] = {}
    conflicts = 0
    for field_name in _MERGED_FIELDS:
        name_value = getattr(name_draft, field_name)
        name_value = None if name_value in (None, "") else name_value
        folder_value = getattr(folder_draft, field_name)
        folder_value = None if folder_value in (None, "") else folder_value
        value, source = choose_prefer_name(name_value, folder_value)
        merged_values[field_name] = value
        sources[field_name] = source
        if name_value is not None and folder_value is not None and name_value != folder_value:
            conflicts += 1

    level = name_draft.level
    if any(source == SOURCE_FOLDER for source in sources.values()):
        level = merge_levels(level, folder_draft.level)
    if conflicts:
        level = downgrade(level)

    title = str(merged_values["title"] or "")
    season = _as_int(merged_values["season"])
    episode = _as_int(merged_values["episode"])
    segment = merged_values["segment"] if isinstance(merged_values["segment"], Segment) else None
    fansub = merged_values["fansub"] if isinstance(merged_values["fansub"], str) else None

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
        evidence=dict(sources),
    )


def apply_release_progress(draft: L1Draft, context: ParseContext | None) -> L1Draft:
    """An episode beyond context.release_progress must drop to LOW."""
    if context is None or context.release_progress is None:
        return draft
    if draft.episode is not None and draft.episode > context.release_progress:
        return replace(draft, level=Confidence.LOW)
    return draft


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
