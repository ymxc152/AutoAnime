"""L1Draft: the mutable-stage draft produced inside L1 before arbitration.

L1Draft is internal to the L1 pipeline. The public contract stays
``ParseResult`` (autoanime.core.interfaces); conversion happens here so the
dialect recognizers never build ParseResult by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseResult
from autoanime.pipeline.l1.confidence import confidence_for, downgrade, missing_fields_for


@dataclass(frozen=True)
class L1Draft:
    title: str
    season: int | None = None
    episode: int | None = None
    segment: Segment | None = None
    fansub: str | None = None
    level: Confidence = Confidence.MEDIUM
    missing_fields: tuple[str, ...] = ()
    evidence: dict[str, str] = field(default_factory=dict)

    @property
    def confidence(self) -> float:
        return confidence_for(self.level)

    def downgraded(self, steps: int = 1) -> L1Draft:
        """A copy with the level dropped by the given steps (LOW stays LOW)."""
        return replace(self, level=downgrade(self.level, steps))

    def finalized(self) -> L1Draft:
        """Recompute missing_fields and clamp a HIGH draft that is incomplete.

        Dialect-specific downgrades (conflicts, noise) are preserved: only a
        HIGH level is demoted to MEDIUM when fields are still missing.
        """
        missing = missing_fields_for(
            title=self.title,
            season=self.season,
            episode=self.episode,
            segment=self.segment,
        )
        level = self.level
        if missing and level is Confidence.HIGH:
            level = Confidence.MEDIUM
        return replace(self, missing_fields=missing, level=level)

    def to_parse_result(self) -> ParseResult:
        """Build the contract-level ParseResult; segment and title are mandatory."""
        if not self.title:
            raise ValueError("L1Draft.title must be a non-empty string")
        if self.segment is None:
            raise ValueError("L1Draft.segment must be set before building a ParseResult")
        return ParseResult(
            title=self.title,
            season=self.season,
            episode=self.episode,
            segment=self.segment,
            fansub=self.fansub,
            level=self.level,
            confidence=self.confidence,
            missing_fields=self.missing_fields,
            evidence=dict(self.evidence),
        )
