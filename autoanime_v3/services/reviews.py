"""Review resolution that produces a new immutable plan revision."""

import json
import math
import re
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.repositories.plans import PlanRepository
from autoanime_v3.db.repositories.reviews import review_from_row
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import (
    InvalidStateError,
    NotFoundError,
    PlanConflictError,
    ValidationError,
)
from autoanime_v3.models import MediaFile as CoreMediaFile, Resolution
from autoanime_v3.planner import build_plan
from autoanime_v3.services.memory import ShowMemoryService
from autoanime_v3.services.roots import normalize_windows_path


MEDIA_TYPES = {"episode", "movie", "special"}
RESOLUTION_FIELDS = {
    "title",
    "media_type",
    "season",
    "episode",
    "is_movie",
    "release_tag",
    "manual_lock",
}
EPISODE_TOKEN = re.compile(r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$")
INTEGER_TOKEN = re.compile(r"^\d+$")
DECIMAL_TOKEN = re.compile(r"^\d+\.\d+$")


def _invalid(field, message):
    raise ValidationError(message, {"field": field})


def _normalize_season(value):
    if isinstance(value, bool):
        _invalid("season", "Season must be a non-negative integer")
    if isinstance(value, str):
        value = value.strip()
        if not INTEGER_TOKEN.fullmatch(value):
            _invalid("season", "Season must be a non-negative integer")
        value = int(value)
    if not isinstance(value, int) or value < 0:
        _invalid("season", "Season must be a non-negative integer")
    return value


def _normalize_episode(value):
    if isinstance(value, bool) or value is None:
        _invalid("episode", "Episode must be a non-negative number or safe episode label")
    if isinstance(value, int):
        if value < 0:
            _invalid("episode", "Episode must be non-negative")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0:
            _invalid("episode", "Episode must be a finite non-negative number")
        return value
    if not isinstance(value, str):
        _invalid("episode", "Episode must be a number or string label")
    token = value.strip()
    if not token or len(token) > 32 or not EPISODE_TOKEN.fullmatch(token):
        _invalid("episode", "Episode label contains unsupported characters")
    if INTEGER_TOKEN.fullmatch(token):
        return int(token)
    if DECIMAL_TOKEN.fullmatch(token):
        return float(token)
    return token


def normalize_resolution(resolution_data):
    if not isinstance(resolution_data, dict):
        _invalid("resolution", "Resolution must be an object")
    unknown_fields = sorted(set(resolution_data) - RESOLUTION_FIELDS)
    if unknown_fields:
        _invalid(unknown_fields[0], "Unsupported resolution field")

    title = resolution_data.get("title")
    if not isinstance(title, str) or not title.strip():
        _invalid("title", "Title is required")
    title = title.strip()
    if len(title) > 200:
        _invalid("title", "Title is too long")

    has_explicit_type = "media_type" in resolution_data
    explicit_type = resolution_data.get("media_type")
    legacy_movie = resolution_data.get("is_movie")
    if legacy_movie is not None and not isinstance(legacy_movie, bool):
        _invalid("is_movie", "is_movie must be a boolean")
    if has_explicit_type and not isinstance(explicit_type, str):
        _invalid("media_type", "Media type must be a string")
    media_type = explicit_type if has_explicit_type else ("movie" if legacy_movie else "episode")
    if media_type not in MEDIA_TYPES:
        _invalid("media_type", "Media type must be episode, movie, or special")
    if has_explicit_type and legacy_movie is not None and legacy_movie != (media_type == "movie"):
        _invalid("is_movie", "is_movie conflicts with media_type")

    release_tag = resolution_data.get("release_tag", "")
    if not isinstance(release_tag, str):
        _invalid("release_tag", "Release tag must be a string")
    release_tag = release_tag.strip()
    if len(release_tag) > 100:
        _invalid("release_tag", "Release tag is too long")

    manual_lock = resolution_data.get("manual_lock", True)
    if not isinstance(manual_lock, bool):
        _invalid("manual_lock", "Manual lock must be a boolean")

    normalized = {
        "title": title,
        "media_type": media_type,
        "is_movie": media_type == "movie",
        "release_tag": release_tag,
        "manual_lock": manual_lock,
    }
    if media_type == "movie":
        for field in ("season", "episode"):
            if field in resolution_data and resolution_data[field] not in (None, ""):
                _invalid(field, "Movie resolutions must not include season or episode")
        return normalized

    if media_type == "episode" and resolution_data.get("season") in (None, ""):
        _invalid("season", "Season is required for episodes")
    season = _normalize_season(resolution_data.get("season", 0))
    if resolution_data.get("episode") in (None, ""):
        _invalid("episode", "Episode is required")
    normalized["season"] = season
    normalized["episode"] = _normalize_episode(resolution_data["episode"])
    return normalized


class ReviewService:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)

    def _query(self, sql, params=()):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def list_open(self):
        return tuple(
            review_from_row(row)
            for row in self._query("SELECT * FROM review_items WHERE status = 'open' ORDER BY id")
        )

    def get(self, review_id):
        rows = self._query("SELECT * FROM review_items WHERE id = ?", (review_id,))
        if not rows:
            raise NotFoundError("Review item does not exist", {"id": review_id})
        return review_from_row(rows[0])

    def resolve(self, review_id, resolution_data, user_id=None):
        normalized = normalize_resolution(resolution_data)
        with SqliteUnitOfWork(self.database_path) as uow:
            review_row = uow.connection.execute(
                "SELECT * FROM review_items WHERE id = ?", (review_id,)
            ).fetchone()
            if review_row is None:
                raise NotFoundError("Review item does not exist", {"id": review_id})
            claimed = uow.connection.execute(
                """
                UPDATE review_items SET status = 'resolving', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'open'
                """,
                (review_id,),
            ).rowcount
            if claimed != 1:
                raise InvalidStateError("Review item is not open")
            review = review_from_row(review_row)
            run = uow.connection.execute(
                "SELECT * FROM scan_runs WHERE id = ?", (review.scan_run_id,)
            ).fetchone()
            profile = uow.connection.execute(
                "SELECT * FROM scan_profiles WHERE id = ?", (run["profile_id"],)
            ).fetchone()
            library_path = Path(
                uow.connection.execute(
                    "SELECT path FROM storage_roots WHERE id = ?", (profile["library_root_id"],)
                ).fetchone()[0]
            )
            latest = uow.connection.execute(
                "SELECT * FROM plans WHERE scan_run_id = ? ORDER BY revision DESC LIMIT 1",
                (review.scan_run_id,),
            ).fetchone()
            new_revision = int(latest["revision"]) + 1
            cursor = uow.connection.execute(
                """
                INSERT INTO plans(
                    scan_run_id, profile_id, profile_revision, rule_version,
                    library_revision, revision, status, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'ready', ?)
                """,
                (
                    latest["scan_run_id"],
                    latest["profile_id"],
                    latest["profile_revision"],
                    latest["rule_version"],
                    latest["library_revision"],
                    new_revision,
                    latest["summary_json"],
                ),
            )
            new_plan_id = int(cursor.lastrowid)
            uow.connection.execute(
                """
                INSERT INTO plan_items(
                    plan_id, source_location_id, destination_root_id,
                    destination_relative_path, action, reason, risk_level,
                    source_file_index, source_size, source_mtime_ns, source_sha256,
                    identification_snapshot_json, execution_status
                )
                SELECT ?, source_location_id, destination_root_id,
                    destination_relative_path, action, reason, risk_level,
                    source_file_index, source_size, source_mtime_ns, source_sha256,
                    identification_snapshot_json, execution_status
                FROM plan_items
                WHERE plan_id = ?
                  AND source_location_id NOT IN (
                      SELECT id FROM file_locations WHERE media_file_id = ?
                  )
                """,
                (new_plan_id, latest["id"], review.media_file_id),
            )
            scan_item = uow.connection.execute(
                "SELECT * FROM scan_items WHERE scan_run_id = ? AND media_file_id = ?",
                (review.scan_run_id, review.media_file_id),
            ).fetchone()
            snapshot = json.loads(scan_item["snapshot_json"])
            core_media = CoreMediaFile(
                path=Path(snapshot["path"]),
                input_root=Path(snapshot["path"]).parent,
                context_name=snapshot["context_name"],
                relative_path=snapshot["relative_path"],
                size=int(snapshot["size"]),
                mtime_ns=int(snapshot["mtime_ns"]),
            )
            accepted = Resolution(
                media=core_media,
                canonical_title=normalized["title"],
                season=normalized.get("season"),
                episode=normalized.get("episode"),
                is_movie=normalized["is_movie"],
                confidence=1.0,
                accepted=True,
                release_tag=normalized["release_tag"],
                fingerprint="manual-review-%s" % review_id,
                media_type=normalized["media_type"],
                manual_lock=bool(normalized.get("manual_lock", True)),
            )
            entries = build_plan([accepted], library_path)
            existing_destinations = {
                (
                    int(row["destination_root_id"]),
                    str(row["destination_relative_path"]).casefold(),
                )
                for row in uow.connection.execute(
                    """
                    SELECT destination_root_id, destination_relative_path
                    FROM plan_items WHERE plan_id = ?
                    """,
                    (new_plan_id,),
                ).fetchall()
            }
            prepared_entries = []
            for entry in entries:
                if entry.destination is None:
                    raise InvalidStateError(
                        "Resolved review produced an incomplete plan entry",
                        {"path": str(entry.source)},
                    )
                relative_destination = str(entry.destination.relative_to(library_path))
                destination_key = (
                    int(profile["library_root_id"]),
                    relative_destination.casefold(),
                )
                if destination_key in existing_destinations:
                    raise PlanConflictError(
                        "Resolved review destination conflicts with an existing plan item",
                        {"field": "destination", "path": relative_destination},
                    )
                existing_destinations.add(destination_key)
                source_fact = uow.connection.execute(
                    """
                    SELECT fl.id AS source_location_id, mf.file_index, mf.size,
                           mf.mtime_ns, mf.sha256
                    FROM file_locations fl
                    JOIN media_files mf ON mf.id = fl.media_file_id
                    WHERE fl.normalized_path = ?
                      AND fl.role = 'source' AND fl.state = 'present'
                    ORDER BY fl.id DESC LIMIT 1
                    """,
                    (normalize_windows_path(entry.source),),
                ).fetchone()
                if source_fact is None:
                    raise NotFoundError(
                        "Plan entry source has not been observed",
                        {"path": str(entry.source)},
                    )
                prepared_entries.append(
                    (entry, source_fact, relative_destination)
                )

            identification_snapshot = json.dumps(accepted.to_dict(), ensure_ascii=False)
            aliases = [normalized["title"], snapshot.get("context_name", "")]
            payload = review.payload if isinstance(review.payload, dict) else {}
            aliases.append(str(payload.get("title") or ""))
            aliases.append(Path(snapshot.get("path", "")).stem)
            ShowMemoryService(self.database_path).remember(
                aliases,
                normalized["title"],
                source="review",
                confidence=100,
                connection=uow.connection,
            )
            for entry, source_fact, relative_destination in prepared_entries:
                action = str(profile["mode"]) if entry.action == "organize" else entry.action
                is_conflict = entry.action == "conflict"
                uow.connection.execute(
                    """
                    INSERT INTO plan_items(
                        plan_id, source_location_id, destination_root_id,
                        destination_relative_path, action, reason, risk_level,
                        source_file_index, source_size, source_mtime_ns, source_sha256,
                        identification_snapshot_json, execution_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_plan_id,
                        source_fact["source_location_id"],
                        profile["library_root_id"],
                        relative_destination,
                        action,
                        entry.reason or "manual_review",
                        "high" if is_conflict else "normal",
                        source_fact["file_index"],
                        source_fact["size"],
                        source_fact["mtime_ns"],
                        source_fact["sha256"],
                        identification_snapshot,
                        "conflict" if is_conflict else "pending",
                    ),
                )
            resolved = uow.connection.execute(
                """
                UPDATE review_items
                SET status = 'resolved', resolution_json = ?, resolved_by = ?,
                    resolved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'resolving'
                """,
                (json.dumps(normalized, ensure_ascii=False), user_id, review_id),
            ).rowcount
            if resolved != 1:
                raise InvalidStateError("Review item is not being resolved")
            uow.commit()
        from autoanime_v3.services.plans import PlanService

        plans = PlanService(self.database_path)
        automatic = plans.auto_apply_safe(new_plan_id)
        return automatic[0] if automatic is not None else plans.get(new_plan_id)
