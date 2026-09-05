"""Evidence arbitration — thin re-export of the ``l3.arbiter`` decision table.

The decision table lives in ``autoanime.pipeline.l3.arbiter`` (T1/T4); this
module keeps the historical import path stable for callers that address the
arbiter at the pipeline top level.
"""

from autoanime.pipeline.l3.arbiter import (
    AUDIT_FIELD_CONFLICT,
    AUDIT_L3_UNAVAILABLE,
    AUDIT_LEVEL_UPGRADED,
    AUDIT_SEASON_DISAMBIGUATED,
    AUDIT_SEASON_DISAMBIGUATION_REJECTED,
    EVIDENCE_PRIORITY,
    RULE_R4_CONSISTENCY,
    RULE_R5_BASE_MEDIUM,
    RULE_R5_VERIFIED,
    ArbiterAudit,
    ArbiterInput,
    ArbiterVerdict,
    FieldResolution,
    arbitrate,
    disambiguate_season,
    evidence_rank,
    resolve_field,
    title_shape_matches,
    upgrade_level,
)

__all__ = [
    "AUDIT_FIELD_CONFLICT",
    "AUDIT_L3_UNAVAILABLE",
    "AUDIT_LEVEL_UPGRADED",
    "AUDIT_SEASON_DISAMBIGUATED",
    "AUDIT_SEASON_DISAMBIGUATION_REJECTED",
    "EVIDENCE_PRIORITY",
    "RULE_R4_CONSISTENCY",
    "RULE_R5_BASE_MEDIUM",
    "RULE_R5_VERIFIED",
    "ArbiterAudit",
    "ArbiterInput",
    "ArbiterVerdict",
    "FieldResolution",
    "arbitrate",
    "disambiguate_season",
    "evidence_rank",
    "resolve_field",
    "title_shape_matches",
    "upgrade_level",
]
