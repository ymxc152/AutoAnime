"""L2 memory layer: shared contracts and pure infrastructure (PR4 T1).

The L2 memory is a fixed pipeline component, not a registry entry: key
derivation, placeholder handling, trust scoring, level fusion and bypass
matching are pure functions here, and every DB session stays inside the
store layer (T2). Modules:

- keys:         two-level key derivation (series/exact) and stable hashing
- placeholders: title shape templates ({season}/{ep}) and backfill
- trust:        trust score, 0.5/0.8 thresholds, MEDIUM+hit->HIGH fusion
- bypass:       pattern_hash normalization and matching (pure side)
- draft:        MemoryHit draft and the final ParseResult construction

T2/T3 land the storage and learning sides; T4 the recognizer and
orchestrator segment. This package defines only what they all share.
"""

from autoanime.pipeline.l2.bypass import (
    is_bypassed,
    normalize_pattern,
    pattern_hash,
)
from autoanime.pipeline.l2.draft import (
    KEY_LEVEL_EVIDENCE,
    MEMORY_EVIDENCE,
    MemoryHit,
    apply_memory_hit,
)
from autoanime.pipeline.l2.keys import (
    KEY_LEVEL_EXACT,
    KEY_LEVEL_SERIES,
    fansub_norm,
    key_hash,
    level1_key,
    level2_key,
    stable_hash,
)
from autoanime.pipeline.l2.placeholders import (
    EPISODE_PLACEHOLDER,
    SEASON_PLACEHOLDER,
    backfill_title,
    build_title_shape,
)
from autoanime.pipeline.l2.trust import (
    TRUST_FUSION_THRESHOLD,
    TRUST_PENDING_THRESHOLD,
    can_fuse,
    eligible_for_memory,
    fused_level,
    should_demote_to_pending,
    trust_score,
)

__all__ = [
    "EPISODE_PLACEHOLDER",
    "KEY_LEVEL_EVIDENCE",
    "KEY_LEVEL_EXACT",
    "KEY_LEVEL_SERIES",
    "MEMORY_EVIDENCE",
    "SEASON_PLACEHOLDER",
    "TRUST_FUSION_THRESHOLD",
    "TRUST_PENDING_THRESHOLD",
    "MemoryHit",
    "apply_memory_hit",
    "backfill_title",
    "build_title_shape",
    "can_fuse",
    "eligible_for_memory",
    "fansub_norm",
    "fused_level",
    "is_bypassed",
    "key_hash",
    "level1_key",
    "level2_key",
    "normalize_pattern",
    "pattern_hash",
    "should_demote_to_pending",
    "stable_hash",
    "trust_score",
]
