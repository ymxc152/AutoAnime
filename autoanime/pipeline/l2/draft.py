"""L2 hit draft and the final ParseResult construction.

``MemoryHit`` is the draft of one memory lookup outcome; ``from_stored_result``
recovers the typed fields from a ``parse_memory.result`` JSON dict.
``apply_memory_hit`` merges a hit into an L1 ParseResult under the PR4
contract:

- memory is an enhancement layer: it only fills absent fields and never
  overwrites an existing L1 value; fields whose L1 evidence is ``name`` or
  ``folder`` are untouchable in any case (filename-first continues to hold);
- every filled field's evidence becomes ``memory``;
- on any hit the evidence gains ``key_level``: ``memory:1`` / ``memory:2``;
- a trusted hit (trust >= 0.8) that filled at least one field raises an L1
  MEDIUM result to HIGH; below the fusion threshold only evidence is
  supplemented and the level is unchanged.

Pure functions only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from autoanime.core.enums import Segment
from autoanime.core.interfaces import ParseResult
from autoanime.pipeline.l1.confidence import confidence_for, missing_fields_for
from autoanime.pipeline.l1.context import SOURCE_FOLDER, SOURCE_NAME
from autoanime.pipeline.l2.trust import can_fuse, fused_level

MEMORY_EVIDENCE = "memory"
KEY_LEVEL_EVIDENCE = "key_level"

_PROTECTED_EVIDENCE = frozenset({SOURCE_NAME, SOURCE_FOLDER})
_FILLED_FIELDS: tuple[str, ...] = ("title", "season", "episode", "segment", "fansub")


@dataclass(frozen=True)
class MemoryHit:
    """One memory lookup outcome, ready to merge into an L1 result."""

    key_level: int
    trust: float
    title: str | None = None
    season: int | None = None
    episode: int | None = None
    segment: Segment | None = None
    fansub: str | None = None

    @classmethod
    def from_stored_result(
        cls, stored: Mapping[str, object], *, key_level: int, trust: float
    ) -> MemoryHit:
        """Recover the typed hit from a ``parse_memory.result`` JSON dict.

        Absent, mistyped or empty entries are treated as unknown rather than
        fatal: the stored dict is written by the learning side (T3) and read
        defensively here.
        """
        return cls(
            key_level=key_level,
            trust=trust,
            title=_as_str(stored.get("title")),
            season=_as_int(stored.get("season")),
            episode=_as_int(stored.get("episode")),
            segment=_as_segment(stored.get("segment")),
            fansub=_as_str(stored.get("fansub")),
        )


def apply_memory_hit(result: ParseResult, hit: MemoryHit) -> ParseResult:
    """Merge one memory hit into an L1 result under the PR4 evidence contract."""
    evidence = dict(result.evidence)

    new_title = result.title
    new_season = result.season
    new_episode = result.episode
    new_segment = result.segment
    new_fansub = result.fansub
    filled = False

    if hit.title is not None and _fillable(result.title, result.evidence, "title"):
        new_title = hit.title
        evidence["title"] = MEMORY_EVIDENCE
        filled = True
    if hit.season is not None and _fillable(result.season, result.evidence, "season"):
        new_season = hit.season
        evidence["season"] = MEMORY_EVIDENCE
        filled = True
    if hit.episode is not None and _fillable(result.episode, result.evidence, "episode"):
        new_episode = hit.episode
        evidence["episode"] = MEMORY_EVIDENCE
        filled = True
    if hit.segment is not None and _fillable(result.segment, result.evidence, "segment"):
        new_segment = hit.segment
        evidence["segment"] = MEMORY_EVIDENCE
        filled = True
    if hit.fansub is not None and _fillable(result.fansub, result.evidence, "fansub"):
        new_fansub = hit.fansub
        evidence["fansub"] = MEMORY_EVIDENCE
        filled = True

    evidence[KEY_LEVEL_EVIDENCE] = f"memory:{hit.key_level}"

    level = result.level
    if filled and can_fuse(hit.trust):
        level = fused_level(result.level, trusted_hit=True)

    return ParseResult(
        title=new_title,
        season=new_season,
        episode=new_episode,
        segment=new_segment,
        fansub=new_fansub,
        level=level,
        confidence=confidence_for(level),
        missing_fields=missing_fields_for(
            title=new_title, season=new_season, episode=new_episode, segment=new_segment
        ),
        evidence=evidence,
    )


def _fillable(current: object, evidence: Mapping[str, str], field_name: str) -> bool:
    """Absent and unprotected: memory may fill it."""
    if current is None or current == "":
        return evidence.get(field_name) not in _PROTECTED_EVIDENCE
    return False


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_segment(value: object) -> Segment | None:
    if isinstance(value, Segment):
        return value
    if isinstance(value, str):
        try:
            return Segment(value)
        except ValueError:
            return None
    return None
