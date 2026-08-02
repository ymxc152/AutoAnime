"""SQLAlchemy Core schema for the Web console database."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utc_columns():
    return (
        Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
        Column("updated_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    )


schema_migrations = Table(
    "schema_migrations",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("applied_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(128), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("1")),
    Column("password_changed_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    *utc_columns()
)

user_sessions = Table(
    "user_sessions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", String(128), nullable=False, unique=True),
    Column("csrf_hash", String(128), nullable=False),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("last_seen_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("expires_at", String(32), nullable=False),
    Column("revoked_at", String(32)),
    Column("client_ip", String(64)),
    Column("user_agent", Text),
)

login_attempts = Table(
    "login_attempts",
    metadata,
    Column("attempt_key", String(128), primary_key=True),
    Column("failure_count", Integer, nullable=False, server_default=text("0")),
    Column("window_started_at", String(32), nullable=False),
    Column("locked_until", String(32)),
    Column("updated_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

app_settings = Table(
    "app_settings",
    metadata,
    Column("key", String(128), primary_key=True),
    Column("value_json", Text, nullable=False),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    Column("updated_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

secret_settings = Table(
    "secret_settings",
    metadata,
    Column("key", String(128), primary_key=True),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("provider", String(32), nullable=False),
    Column("updated_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("actor_user_id", ForeignKey("users.id", ondelete="SET NULL")),
    Column("action", String(128), nullable=False),
    Column("object_type", String(64), nullable=False),
    Column("object_id", String(128)),
    Column("before_json", Text),
    Column("after_json", Text),
    Column("reason", Text),
    Column("trace_id", String(64)),
    Column("client_ip", String(64)),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

storage_roots = Table(
    "storage_roots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("path", Text, nullable=False),
    Column("normalized_path", Text, nullable=False, unique=True),
    Column("volume_serial", String(64)),
    Column("filesystem_type", String(32)),
    Column("enabled", Boolean, nullable=False, server_default=text("1")),
    Column("health_status", String(32), nullable=False, server_default=text("'unknown'")),
    Column("last_checked_at", String(32)),
    *utc_columns(),
)

scan_profiles = Table(
    "scan_profiles",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(128), nullable=False, unique=True),
    Column("source_root_id", ForeignKey("storage_roots.id", ondelete="RESTRICT"), nullable=False),
    Column("library_root_id", ForeignKey("storage_roots.id", ondelete="RESTRICT"), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("execution_policy", String(32), nullable=False),
    Column("min_confidence", Integer, nullable=False, server_default=text("80")),
    Column("stability_seconds", Integer, nullable=False, server_default=text("30")),
    Column("watch_enabled", Boolean, nullable=False, server_default=text("0")),
    Column("enabled", Boolean, nullable=False, server_default=text("1")),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    CheckConstraint("source_root_id <> library_root_id", name="different_roots"),
    *utc_columns(),
)

profile_rules = Table(
    "profile_rules",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", ForeignKey("scan_profiles.id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("include_globs_json", Text, nullable=False, server_default=text("'[]'")),
    Column("exclude_globs_json", Text, nullable=False, server_default=text("'[]'")),
    Column("media_extensions_json", Text, nullable=False, server_default=text("'[]'")),
    Column("subtitle_extensions_json", Text, nullable=False, server_default=text("'[]'")),
    Column("temporary_suffixes_json", Text, nullable=False, server_default=text("'[]'")),
    Column("ignored_directories_json", Text, nullable=False, server_default=text("'[]'")),
    Column("minimum_size", Integer, nullable=False, server_default=text("0")),
    *utc_columns(),
)

schedules = Table(
    "schedules",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", ForeignKey("scan_profiles.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("schedule_json", Text, nullable=False),
    Column("timezone", String(64), nullable=False),
    Column("next_run_at", String(32)),
    Column("last_run_at", String(32)),
    Column("enabled", Boolean, nullable=False, server_default=text("1")),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    *utc_columns(),
)

webhook_sources = Table(
    "webhook_sources",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(128), nullable=False),
    Column("downloader", String(64), nullable=False),
    Column("token_hash", String(128), nullable=False, unique=True),
    Column("profile_id", ForeignKey("scan_profiles.id", ondelete="CASCADE"), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=text("1")),
    Column("last_called_at", String(32)),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    *utc_columns(),
)

resource_leases = Table(
    "resource_leases",
    metadata,
    Column("resource_key", String(255), primary_key=True),
    Column("owner", String(128), nullable=False),
    Column("lease_until", String(32), nullable=False),
    Column("heartbeat_at", String(32), nullable=False),
    Column("revision", Integer, nullable=False, server_default=text("1")),
)

shows = Table(
    "shows",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("canonical_title", Text, nullable=False),
    Column("normalized_key", Text, nullable=False, unique=True),
    Column("status", String(32), nullable=False, server_default=text("'unknown'")),
    Column("title_locked", Boolean, nullable=False, server_default=text("0")),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    *utc_columns(),
)

seasons = Table(
    "seasons",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("show_id", ForeignKey("shows.id", ondelete="CASCADE"), nullable=False),
    Column("season_number", Integer, nullable=False),
    Column("display_title", Text),
    Column("expected_episode_count", Integer),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    UniqueConstraint("show_id", "season_number"),
    *utc_columns(),
)

episodes = Table(
    "episodes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("season_id", ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False),
    Column("episode_number", String(32), nullable=False),
    Column("episode_type", String(32), nullable=False, server_default=text("'episode'")),
    Column("display_title", Text),
    Column("sort_value", Integer, nullable=False),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    UniqueConstraint("season_id", "episode_number", "episode_type"),
    *utc_columns(),
)

media_files = Table(
    "media_files",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("size", Integer, nullable=False),
    Column("mtime_ns", Integer, nullable=False),
    Column("volume_serial", String(64)),
    Column("file_index", String(64)),
    Column("sha256", String(64)),
    Column("media_kind", String(32), nullable=False),
    Column("generation_status", String(32), nullable=False, server_default=text("'current'")),
    *utc_columns(),
)

file_locations = Table(
    "file_locations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("media_file_id", ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False),
    Column("root_id", ForeignKey("storage_roots.id", ondelete="RESTRICT"), nullable=False),
    Column("path", Text, nullable=False),
    Column("normalized_path", Text, nullable=False),
    Column("role", String(16), nullable=False),
    Column("state", String(16), nullable=False),
    Column("first_seen_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("last_seen_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index(
    "uq_file_locations_present_path",
    file_locations.c.normalized_path,
    unique=True,
    sqlite_where=file_locations.c.state == "present",
)

media_assignments = Table(
    "media_assignments",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("media_file_id", ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("show_id", ForeignKey("shows.id", ondelete="SET NULL")),
    Column("season_id", ForeignKey("seasons.id", ondelete="SET NULL")),
    Column("episode_id", ForeignKey("episodes.id", ondelete="SET NULL")),
    Column("release_label", String(128)),
    Column("version_label", String(128)),
    Column("title_locked", Boolean, nullable=False, server_default=text("0")),
    Column("season_locked", Boolean, nullable=False, server_default=text("0")),
    Column("episode_locked", Boolean, nullable=False, server_default=text("0")),
    Column("version_locked", Boolean, nullable=False, server_default=text("0")),
    Column("source", String(32), nullable=False),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    *utc_columns(),
)

identification_results = Table(
    "identification_results",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("media_file_id", ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False),
    Column("decision_fingerprint", String(128), nullable=False),
    Column("parser_version", String(64), nullable=False),
    Column("rule_version", String(64), nullable=False),
    Column("title", Text),
    Column("season_number", Integer),
    Column("episode_number", String(32)),
    Column("media_type", String(32)),
    Column("confidence", Integer, nullable=False),
    Column("accepted", Boolean, nullable=False, server_default=text("0")),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

identification_evidence = Table(
    "identification_evidence",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("result_id", ForeignKey("identification_results.id", ondelete="CASCADE"), nullable=False),
    Column("agent", String(64), nullable=False),
    Column("field", String(64), nullable=False),
    Column("value_json", Text),
    Column("confidence", Integer),
    Column("detail_json", Text),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

metadata_records = Table(
    "metadata_records",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("show_id", ForeignKey("shows.id", ondelete="CASCADE"), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("provider_id", String(128), nullable=False),
    Column("poster_url", Text),
    Column("poster_cache_path", Text),
    Column("synopsis", Text),
    Column("broadcast_status", String(64)),
    Column("fetched_at", String(32), nullable=False),
    Column("expires_at", String(32)),
    Column("response_digest", String(128)),
    UniqueConstraint("provider", "provider_id"),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job_type", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("priority", Integer, nullable=False, server_default=text("0")),
    Column("payload_json", Text, nullable=False),
    Column("idempotency_key", String(128), unique=True),
    Column("progress_current", Integer, nullable=False, server_default=text("0")),
    Column("progress_total", Integer, nullable=False, server_default=text("0")),
    Column("current_stage", String(128)),
    Column("error_code", String(64)),
    Column("error_summary", Text),
    Column("lease_owner", String(128)),
    Column("lease_until", String(32)),
    Column("heartbeat_at", String(32)),
    Column("requested_by", ForeignKey("users.id", ondelete="SET NULL")),
    Column("cancel_requested_at", String(32)),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("started_at", String(32)),
    Column("finished_at", String(32)),
)

job_events = Table(
    "job_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job_id", ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("level", String(16), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("message", Text, nullable=False),
    Column("payload_json", Text),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("job_id", "sequence"),
)

scan_runs = Table(
    "scan_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job_id", ForeignKey("jobs.id", ondelete="SET NULL")),
    Column("profile_id", ForeignKey("scan_profiles.id", ondelete="RESTRICT"), nullable=False),
    Column("profile_revision", Integer, nullable=False),
    Column("rule_version", String(64), nullable=False),
    Column("scope_json", Text, nullable=False),
    Column("statistics_json", Text, nullable=False, server_default=text("'{}'")),
    Column("started_at", String(32), nullable=False),
    Column("finished_at", String(32)),
)

scan_items = Table(
    "scan_items",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scan_run_id", ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False),
    Column("media_file_id", ForeignKey("media_files.id", ondelete="SET NULL")),
    Column("path", Text, nullable=False),
    Column("normalized_path", Text, nullable=False),
    Column("snapshot_json", Text, nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("reason", String(128)),
    UniqueConstraint("scan_run_id", "normalized_path"),
)

review_items = Table(
    "review_items",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scan_run_id", ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=False),
    Column("media_file_id", ForeignKey("media_files.id", ondelete="SET NULL")),
    Column("review_type", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("dedup_key", String(128), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("resolution_json", Text),
    Column("resolved_by", ForeignKey("users.id", ondelete="SET NULL")),
    Column("resolved_at", String(32)),
    *utc_columns(),
)
Index(
    "uq_review_items_open_dedup",
    review_items.c.dedup_key,
    unique=True,
    sqlite_where=review_items.c.status == "open",
)

plans = Table(
    "plans",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scan_run_id", ForeignKey("scan_runs.id", ondelete="RESTRICT"), nullable=False),
    Column("profile_id", ForeignKey("scan_profiles.id", ondelete="RESTRICT"), nullable=False),
    Column("profile_revision", Integer, nullable=False),
    Column("rule_version", String(64), nullable=False),
    Column("library_revision", Integer, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("summary_json", Text, nullable=False),
    Column("approved_by", ForeignKey("users.id", ondelete="SET NULL")),
    Column("approved_at", String(32)),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("scan_run_id", "revision"),
)

plan_items = Table(
    "plan_items",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("plan_id", ForeignKey("plans.id", ondelete="CASCADE"), nullable=False),
    Column("source_location_id", ForeignKey("file_locations.id", ondelete="RESTRICT"), nullable=False),
    Column("destination_root_id", ForeignKey("storage_roots.id", ondelete="RESTRICT"), nullable=False),
    Column("destination_relative_path", Text, nullable=False),
    Column("action", String(16), nullable=False),
    Column("reason", Text),
    Column("risk_level", String(16), nullable=False),
    Column("source_file_index", String(64)),
    Column("source_size", Integer, nullable=False),
    Column("source_mtime_ns", Integer, nullable=False),
    Column("source_sha256", String(64)),
    Column("identification_snapshot_json", Text, nullable=False),
    Column("execution_status", String(32), nullable=False, server_default=text("'pending'")),
    Column("decision", String(16)),
    Column("reject_reason", Text),
    Column("decided_by", ForeignKey("users.id", ondelete="SET NULL")),
    Column("decided_at", String(32)),
    UniqueConstraint("plan_id", "destination_root_id", "destination_relative_path"),
)

operation_batches = Table(
    "operation_batches",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("plan_id", ForeignKey("plans.id", ondelete="RESTRICT")),
    Column("parent_batch_id", ForeignKey("operation_batches.id", ondelete="SET NULL")),
    Column("job_id", ForeignKey("jobs.id", ondelete="SET NULL")),
    Column("kind", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("requested_by", ForeignKey("users.id", ondelete="SET NULL")),
    Column("summary_json", Text, nullable=False, server_default=text("'{}'")),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("finished_at", String(32)),
)

operation_items = Table(
    "operation_items",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", ForeignKey("operation_batches.id", ondelete="CASCADE"), nullable=False),
    Column("plan_item_id", ForeignKey("plan_items.id", ondelete="SET NULL")),
    Column("sequence", Integer, nullable=False),
    Column("action", String(16), nullable=False),
    Column("source_path", Text, nullable=False),
    Column("destination_path", Text, nullable=False),
    Column("source_identity_json", Text, nullable=False),
    Column("result_identity_json", Text),
    Column("result_sha256", String(64)),
    Column("status", String(32), nullable=False),
    Column("error_code", String(64)),
    Column("error_summary", Text),
    Column("compensation_status", String(32)),
    UniqueConstraint("batch_id", "sequence"),
)

change_requests = Table(
    "change_requests",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("target_type", String(64), nullable=False),
    Column("target_id", Integer, nullable=False),
    Column("patch_json", Text, nullable=False),
    Column("old_values_json", Text, nullable=False),
    Column("new_values_json", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("base_revision", Integer, nullable=False),
    Column("plan_id", ForeignKey("plans.id", ondelete="SET NULL")),
    Column("conflict_count", Integer, nullable=False, server_default=text("0")),
    Column("status", String(32), nullable=False),
    Column("requested_by", ForeignKey("users.id", ondelete="SET NULL")),
    *utc_columns(),
)

rule_sets = Table(
    "rule_sets",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(128), nullable=False, unique=True),
    Column("active_revision_id", Integer),
    *utc_columns(),
)

rule_revisions = Table(
    "rule_revisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("rule_set_id", ForeignKey("rule_sets.id", ondelete="CASCADE"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("document_json", Text, nullable=False),
    Column("content_hash", String(64)),
    Column("status", String(32), nullable=False),
    Column("validation_errors_json", Text),
    Column("created_by", ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("rule_set_id", "revision"),
)

backup_records = Table(
    "backup_records",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("path", Text, nullable=False, unique=True),
    Column("kind", String(32), nullable=False),
    Column("size", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("sanitized", Boolean, nullable=False, server_default=text("0")),
    Column("created_by", ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", String(32), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

# SQLite cannot add this cyclic foreign key through ALTER TABLE.  Keeping the
# active revision as an indexed scalar allows rollback while revisions still
# retain a strict foreign key back to their owning rule set.
Index("ix_rule_sets_active_revision_id", rule_sets.c.active_revision_id)
Index("ix_jobs_status_priority", jobs.c.status, jobs.c.priority)
Index("ix_job_events_job_sequence", job_events.c.job_id, job_events.c.sequence)
Index("ix_file_locations_media_file", file_locations.c.media_file_id)
Index("ix_identification_results_media_file", identification_results.c.media_file_id)
Index("ix_plans_status", plans.c.status)
