"""Stable persisted values for the AutoAnime Web console.

Values in this module are stored as strings in SQLite.  Renaming a Python
member is safe; changing its value requires a database migration.
"""

from enum import Enum


class StringEnum(str, Enum):
    def __str__(self):
        return self.value


class RootKind(StringEnum):
    SOURCE = "source"
    LIBRARY = "library"
    OPERATIONS = "operations"
    METADATA_CACHE = "metadata_cache"


class OperationMode(StringEnum):
    LINK = "link"
    COPY = "copy"
    MOVE = "move"


class ExecutionPolicy(StringEnum):
    REVIEW_ALL = "review_all"
    AUTO_APPLY_SAFE = "auto_apply_safe"
    DRY_RUN = "dry_run"


class JobStatus(StringEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ReviewStatus(StringEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"


class PlanStatus(StringEnum):
    DRAFT = "draft"
    READY = "ready"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    STALE = "stale"
    CANCELLED = "cancelled"
    FAILED_ROLLED_BACK = "failed_rolled_back"
    FAILED_PARTIAL_ROLLBACK = "failed_partial_rollback"
    FAILED_NEEDS_ATTENTION = "failed_needs_attention"


class ChangeRequestStatus(StringEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    APPLIED = "applied"
    STALE = "stale"
    REJECTED = "rejected"
    REVERTED = "reverted"


class LocationRole(StringEnum):
    SOURCE = "source"
    LIBRARY = "library"
    STAGING = "staging"


class LocationState(StringEnum):
    PRESENT = "present"
    MISSING = "missing"
    REPLACED = "replaced"
    DELETED = "deleted"


class RuleRevisionStatus(StringEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    ACTIVE = "active"
    RETIRED = "retired"
