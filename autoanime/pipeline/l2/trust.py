"""Trust scoring and level-fusion rules for the L2 memory layer.

Contract thresholds (PR4 decisions):
- trust < 0.5  -> the entry's status is demoted to PENDING;
- trust < 0.8  -> the entry may supplement evidence but does not take part
  in level fusion;
- trust >= 0.8 -> the entry may fuse: an L1 MEDIUM hit may be raised to HIGH.

``trust = hit_count / (hit_count + corrected_count)``. An entry with no
observed corrections (0/0) is trusted: it has never been wrong.

Pure functions only.
"""

from __future__ import annotations

from autoanime.core.enums import Confidence

TRUST_PENDING_THRESHOLD = 0.5
TRUST_FUSION_THRESHOLD = 0.8


def trust_score(hit_count: int, corrected_count: int) -> float:
    """``hit_count / (hit_count + corrected_count)``; 1.0 when both are zero."""
    total = hit_count + corrected_count
    if total == 0:
        return 1.0
    return hit_count / total


def should_demote_to_pending(trust: float) -> bool:
    """Below 0.5 the entry no longer earns ACTIVE status."""
    return trust < TRUST_PENDING_THRESHOLD


def can_fuse(trust: float) -> bool:
    """At or above 0.8 the entry may take part in level fusion."""
    return trust >= TRUST_FUSION_THRESHOLD


def eligible_for_memory(level: Confidence) -> bool:
    """Routing predicate mirroring the PR4 contract: only L1 MEDIUM enters L2.

    HIGH results archive directly; LOW results and L1-None go to the L3
    placeholder. The orchestrator owns the routing; this predicate states it.
    """
    return level is Confidence.MEDIUM


def fused_level(l1_level: Confidence, *, trusted_hit: bool) -> Confidence:
    """MEDIUM + trusted memory hit -> HIGH; every other level passes through.

    LOW never reaches L2 and HIGH never benefits from it, so both are
    returned unchanged by construction.
    """
    if l1_level is Confidence.MEDIUM and trusted_hit:
        return Confidence.HIGH
    return l1_level
