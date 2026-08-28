"""Immutable data transfer objects returned by application services."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class StorageRoot:
    id: int
    kind: str
    path: str
    normalized_path: str
    enabled: bool
    health_status: str
    volume_serial: Optional[str] = None
    filesystem_type: Optional[str] = None


@dataclass(frozen=True)
class RootHealth:
    root_id: int
    exists: bool
    readable: bool
    writable: bool
    health_status: str


@dataclass(frozen=True)
class CreateProfile:
    name: str
    source_root_id: int
    library_root_id: int
    mode: str = "link"
    execution_policy: str = "review_all"
    min_confidence: int = 80
    stability_seconds: int = 30
    watch_enabled: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class ScanProfile:
    id: int
    name: str
    source_root_id: int
    library_root_id: int
    mode: str
    execution_policy: str
    min_confidence: int
    stability_seconds: int
    watch_enabled: bool
    enabled: bool
    revision: int


@dataclass(frozen=True)
class FileLocation:
    id: int
    media_file_id: int
    root_id: int
    path: str
    normalized_path: str
    role: str
    state: str


@dataclass(frozen=True)
class MediaFile:
    id: int
    size: int
    mtime_ns: int
    volume_serial: Optional[str]
    file_index: Optional[str]
    sha256: Optional[str]
    media_kind: str
    generation_status: str
    locations: Tuple[FileLocation, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class UserPublic:
    id: int
    username: str
    is_active: bool


@dataclass(frozen=True)
class SessionCredentials:
    session_token: str
    csrf_token: str
    expires_at: str
    user: UserPublic


@dataclass(frozen=True)
class SecretStatus:
    key: str
    configured: bool
    provider: Optional[str]
    updated_at: Optional[str]


@dataclass(frozen=True)
class Job:
    id: int
    job_type: str
    status: str
    priority: int
    payload: Dict[str, Any]
    idempotency_key: Optional[str]
    progress_current: int
    progress_total: int
    current_stage: Optional[str]
    error_code: Optional[str]
    error_summary: Optional[str]
    lease_owner: Optional[str]
    lease_until: Optional[str]
    cancel_requested: bool
    created_at: str


@dataclass(frozen=True)
class JobEvent:
    id: int
    job_id: int
    sequence: int
    level: str
    event_type: str
    message: str
    payload: Dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class ScanOutcome:
    scan_run_id: int
    plan_id: int
    discovered_count: int
    review_count: int
    plan_item_count: int
    plan_status: str


@dataclass(frozen=True)
class PlanItemView:
    id: int
    source_location_id: int
    source_path: str
    destination_root_id: int
    destination_path: str
    destination_relative_path: str
    action: str
    reason: str
    risk_level: str
    source_size: int
    source_mtime_ns: int
    source_file_index: Optional[str]
    source_sha256: Optional[str]
    execution_status: str
    decision: Optional[str] = None
    reject_reason: Optional[str] = None
    decided_by: Optional[int] = None
    decided_at: Optional[str] = None


@dataclass(frozen=True)
class PlanView:
    id: int
    scan_run_id: int
    profile_id: int
    profile_revision: int
    rule_version: str
    library_revision: int
    revision: int
    status: str
    items: Tuple[PlanItemView, ...]
    profile_snapshot: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewItemView:
    id: int
    scan_run_id: int
    media_file_id: Optional[int]
    review_type: str
    status: str
    payload: Dict[str, Any]
    resolution: Optional[Dict[str, Any]]


@dataclass(frozen=True)
class OperationItemView:
    id: int
    sequence: int
    action: str
    source_path: str
    destination_path: str
    status: str
    result_sha256: Optional[str]
    error_code: Optional[str]
    compensation_status: Optional[str]


@dataclass(frozen=True)
class OperationBatchView:
    id: int
    plan_id: Optional[int]
    parent_batch_id: Optional[int]
    kind: str
    status: str
    summary: Dict[str, Any]
    items: Tuple[OperationItemView, ...]


@dataclass(frozen=True)
class RuleSetView:
    id: int
    name: str
    active_revision_id: Optional[int]


@dataclass(frozen=True)
class RuleRevisionView:
    id: int
    rule_set_id: int
    revision: int
    document: Dict[str, Any]
    content_hash: Optional[str]
    status: str


@dataclass(frozen=True)
class ShowView:
    id: int
    canonical_title: str
    normalized_key: str
    status: str
    title_locked: bool
    revision: int


@dataclass(frozen=True)
class ChangeRequestView:
    id: int
    target_type: str
    target_id: int
    old_values: Dict[str, Any]
    new_values: Dict[str, Any]
    reason: str
    base_revision: int
    status: str


@dataclass(frozen=True)
class BackupRecordView:
    id: int
    path: str
    kind: str
    size: int
    sha256: str
    schema_version: int
    sanitized: bool
    created_at: str
