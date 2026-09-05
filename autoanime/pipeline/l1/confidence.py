"""Confidence grading: level mapping, missing-field rules, and final clamping.

Grading contract (PR3 unified parse contract):
- HIGH: title + season + episode all present with no conflict; legal season
  pack; legal movie.
- MEDIUM: parseable structure but missing season, dubious title
  reconstruction, word-internal dots, or Chinese title without context.
- LOW: field conflicts, episode beyond context.release_progress, heavy noise,
  or unparseable.
"""

from __future__ import annotations

from autoanime.core.enums import Confidence, Segment

_LEVEL_ORDER: tuple[Confidence, ...] = (
    Confidence.HIGH,
    Confidence.MEDIUM,
    Confidence.LOW,
)


def confidence_for(level: Confidence) -> float:
    """Numeric confidence fixed by the contract: HIGH=1.0, MEDIUM=0.6, LOW=0.2."""
    return {_LEVEL_ORDER[0]: 1.0, _LEVEL_ORDER[1]: 0.6, _LEVEL_ORDER[2]: 0.2}[level]


def downgrade(level: Confidence, steps: int = 1) -> Confidence:
    """Drop the level by the given number of steps; LOW stays LOW."""
    index = _LEVEL_ORDER.index(level)
    return _LEVEL_ORDER[min(index + max(steps, 0), len(_LEVEL_ORDER) - 1)]


def merge_levels(*levels: Confidence) -> Confidence:
    """The lowest (most conservative) of the given levels."""
    if not levels:
        raise ValueError("merge_levels requires at least one level")
    return _LEVEL_ORDER[max(_LEVEL_ORDER.index(level) for level in levels)]


def missing_fields_for(
    *,
    title: str | None,
    season: int | None,
    episode: int | None,
    segment: Segment | None,
) -> tuple[str, ...]:
    """Fields whose absence is unexpected for the detected segment.

    A season pack does not require an episode; a movie requires neither.
    An unknown segment is treated as episode-shaped (season + episode both
    expected), the most conservative assumption.
    """
    required: frozenset[str]
    if segment is Segment.EPISODE:
        required = frozenset({"season", "episode"})
    elif segment is Segment.SEASON_PACK:
        required = frozenset({"season"})
    elif segment is Segment.MOVIE:
        required = frozenset()
    else:
        required = frozenset({"season", "episode"})

    missing: list[str] = []
    if not title:
        missing.append("title")
    if "season" in required and season is None:
        missing.append("season")
    if "episode" in required and episode is None:
        missing.append("episode")
    return tuple(missing)


def base_level(
    *, title: str | None, season: int | None, episode: int | None, segment: Segment | None
) -> Confidence:
    """Completeness-only grade before conflict/noise downgrades."""
    if not title:
        return Confidence.LOW
    if missing_fields_for(title=title, season=season, episode=episode, segment=segment):
        return Confidence.MEDIUM
    return Confidence.HIGH
