"""Safe operation batches around the existing low-level executor."""

import json
from datetime import datetime, timezone
from pathlib import Path

from autoanime_v3.cache import ResolutionCache
from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.repositories.library import LibraryRepository
from autoanime_v3.db.repositories.operations import OperationRepository
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import (
    ExecutionPolicyError,
    InvalidStateError,
    NotFoundError,
    PlanConflictError,
    StalePlanError,
)
from autoanime_v3.executor import (
    ExecutionError,
    ExecutionFailure,
    execute_plan,
    rollback as rollback_log,
)
from autoanime_v3.models import MediaFile as CoreMediaFile, PlanEntry, Resolution
from autoanime_v3.normalize import alias_key
from autoanime_v3.path_safety import validate_library_destination
from autoanime_v3.services.plans import PlanService
from autoanime_v3.services.rules import RuleService


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class OperationService:
    def __init__(self, database_path, operation_dir=None):
        self.database_path = Path(database_path)
        self.operation_dir = Path(operation_dir or self.database_path.parent / "operations")
        self.cache_path = self.database_path.with_name(self.database_path.stem + "-resolver.sqlite3")
        run_migrations(self.database_path)

    def get(self, batch_id):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            batch = OperationRepository(connection).get(batch_id)
            if batch is None:
                raise NotFoundError("Operation batch does not exist", {"id": batch_id})
            return batch
        finally:
            connection.close()

    def _load_execution_rows(self, plan_id):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            return connection.execute(
                """
                SELECT pi.*, fl.path AS source_path, fl.media_file_id AS media_file_id,
                       sr.path AS root_path, p.status AS plan_status
                FROM plan_items pi
                JOIN plans p ON p.id = pi.plan_id
                JOIN file_locations fl ON fl.id = pi.source_location_id
                JOIN storage_roots sr ON sr.id = pi.destination_root_id
                WHERE pi.plan_id = ? ORDER BY pi.id
                """,
                (plan_id,),
            ).fetchall()
        finally:
            connection.close()

    def _preflight(self, plan, rows):
        if plan.status not in {"draft", "ready", "approved"}:
            raise InvalidStateError("Only an open plan can be executed")
        if any(row["action"] == "conflict" for row in rows):
            raise PlanConflictError("Plan still contains conflicts")
        prepared = []
        for row in rows:
            # Only explicitly approved items run; undecided and rejected items wait.
            if row["decision"] != "approved":
                continue
            if row["execution_status"] != "pending" or row["action"] in {"conflict", "skip"}:
                continue
            source = Path(row["source_path"])
            destination = Path(row["root_path"]) / row["destination_relative_path"]
            validate_library_destination(Path(row["root_path"]), destination)
            try:
                stat = source.stat()
            except OSError:
                raise StalePlanError("Source file disappeared before execution", {"path": str(source)})
            file_index = str(stat.st_ino) if int(stat.st_ino) else None
            if (
                int(stat.st_size) != int(row["source_size"])
                or int(stat.st_mtime_ns) != int(row["source_mtime_ns"])
                or file_index != row["source_file_index"]
            ):
                raise StalePlanError("Source file changed before execution", {"path": str(source)})
            if destination.exists():
                raise PlanConflictError("Destination is occupied", {"path": str(destination)})
            if row["action"] == "link":
                library_stat = Path(row["root_path"]).stat()
                if int(library_stat.st_dev) != int(stat.st_dev):
                    raise PlanConflictError("Hardlink source and destination are on different volumes")
            snapshot = json.loads(row["identification_snapshot_json"])
            core_media = CoreMediaFile(
                path=source,
                input_root=source.parent,
                context_name=source.parent.name,
                relative_path=source.name,
                size=int(row["source_size"]),
                mtime_ns=int(row["source_mtime_ns"]),
            )
            resolution = Resolution(
                media=core_media,
                canonical_title=str(snapshot.get("title") or ""),
                season=snapshot.get("season"),
                episode=snapshot.get("episode"),
                is_movie=bool(snapshot.get("is_movie", False)),
                confidence=float(snapshot.get("confidence", 1.0)),
                accepted=True,
                release_tag=str(snapshot.get("release_tag") or ""),
                fingerprint=str(snapshot.get("fingerprint") or ""),
                media_type=str(snapshot.get("media_type") or ""),
                manual_lock=bool(snapshot.get("manual_lock", False)),
            )
            prepared.append(
                (
                    row,
                    PlanEntry(
                        source,
                        destination,
                        "organize",
                        resolution,
                        row["reason"] or "",
                        destination_root=Path(row["root_path"]),
                    ),
                )
            )
        return prepared

    def _validate_rule_version(self, plan):
        current_rule_version = RuleService(self.database_path).get_active().content_hash
        if plan.rule_version == current_rule_version:
            return
        with SqliteUnitOfWork(self.database_path) as uow:
            uow.connection.execute(
                "UPDATE plans SET status = 'stale' WHERE id = ? AND status IN ('draft', 'ready', 'approved')",
                (plan.id,),
            )
            uow.commit()
        raise StalePlanError("Active rules changed after plan approval")

    def _claim_execution(self, plan_id, requested_by, rows):
        stale = False
        prior_status = None
        batch_id = None
        with SqliteUnitOfWork(self.database_path) as uow:
            context = uow.connection.execute(
                """
                SELECT p.status, p.profile_revision, p.rule_version, sp.revision, sp.execution_policy
                FROM plans p JOIN scan_profiles sp ON sp.id = p.profile_id
                WHERE p.id = ?
                """,
                (plan_id,),
            ).fetchone()
            if context is None:
                raise NotFoundError("Plan does not exist", {"id": plan_id})
            if str(context["execution_policy"]) == "dry_run":
                raise ExecutionPolicyError(
                    "Dry-run plans cannot be approved or executed",
                    {"plan_id": plan_id, "execution_policy": "dry_run"},
                )
            current_rule_version = RuleService(self.database_path).get_active(
                uow.connection
            ).content_hash
            if str(context["rule_version"]) != current_rule_version:
                uow.connection.execute(
                    "UPDATE plans SET status = 'stale' WHERE id = ? AND status IN ('draft', 'ready', 'approved')",
                    (plan_id,),
                )
                uow.commit()
                stale = True
            elif int(context["revision"]) != int(context["profile_revision"]):
                uow.connection.execute(
                    "UPDATE plans SET status = 'stale' WHERE id = ? AND status IN ('draft', 'ready', 'approved')",
                    (plan_id,),
                )
                uow.commit()
                stale = True
            else:
                if str(context["status"]) not in {"draft", "ready", "approved"}:
                    raise InvalidStateError("Only an open plan can be executed")
                for row in rows:
                    validate_library_destination(
                        Path(row["root_path"]),
                        Path(row["root_path"]) / row["destination_relative_path"],
                    )
                prior_status = str(context["status"])
                claimed = uow.connection.execute(
                    """
                    UPDATE plans SET status = 'executing'
                    WHERE id = ? AND status IN ('draft', 'ready', 'approved')
                    """,
                    (plan_id,),
                ).rowcount
                if claimed != 1:
                    raise InvalidStateError("Plan execution was already claimed")
                cursor = uow.connection.execute(
                    """
                    INSERT INTO operation_batches(plan_id, kind, status, requested_by, summary_json)
                    VALUES (?, 'execute', 'running', ?, '{}')
                    """,
                    (plan_id, requested_by),
                )
                batch_id = int(cursor.lastrowid)
                uow.commit()
        if stale:
            raise StalePlanError("Plan inputs changed after plan approval")
        return batch_id, prior_status

    def execute(self, plan_id, requested_by=None):
        plan = PlanService(self.database_path).get(plan_id)
        self._validate_rule_version(plan)
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            policy = connection.execute(
                "SELECT execution_policy FROM scan_profiles WHERE id = ?",
                (plan.profile_id,),
            ).fetchone()
        finally:
            connection.close()
        if policy is not None and str(policy["execution_policy"]) == "dry_run":
            raise ExecutionPolicyError(
                "Dry-run plans cannot be approved or executed",
                {"plan_id": plan_id, "execution_policy": "dry_run"},
            )
        rows = self._load_execution_rows(plan_id)
        prepared = self._preflight(plan, rows)
        if not prepared:
            raise InvalidStateError("Plan has no executable items")
        modes = {str(row["action"]) for row, unused in prepared}
        if len(modes) != 1:
            raise InvalidStateError("One operation batch must use a single file mode")
        mode = next(iter(modes))
        batch_id, prior_status = self._claim_execution(plan_id, requested_by, rows)
        try:
            with ResolutionCache(self.cache_path) as cache:
                log_path = execute_plan(
                    [entry for unused, entry in prepared],
                    mode,
                    True,
                    cache,
                    self.operation_dir,
                )
        except Exception as error:
            partial_rollback = isinstance(error, ExecutionFailure) and error.partial_rollback
            failure_status = (
                "failed_partial_rollback" if partial_rollback else "failed_rolled_back"
            )
            summary = {"error": str(error)}
            if isinstance(error, ExecutionFailure):
                summary.update(
                    {
                        "log_path": str(error.log_path),
                        "mode": mode,
                        "applied_items": list(error.applied_records),
                        "rollback_results": list(error.rollback_results),
                        "rollback_errors": list(error.rollback_errors),
                    }
                )
            with SqliteUnitOfWork(self.database_path) as uow:
                rollback_by_destination = {
                    result.get("destination"): result
                    for result in getattr(error, "rollback_results", ())
                }
                prepared_by_source = {str(entry.source): (row, entry) for row, entry in prepared}
                for sequence, record in enumerate(
                    getattr(error, "applied_records", ()), start=1
                ):
                    matched = prepared_by_source.get(str(record.get("source")))
                    if matched is None:
                        continue
                    row, entry = matched
                    rollback_result = rollback_by_destination.get(str(entry.destination), {})
                    uow.connection.execute(
                        """
                        INSERT INTO operation_items(
                            batch_id, plan_item_id, sequence, action, source_path,
                            destination_path, source_identity_json, result_identity_json,
                            result_sha256, status, error_code, error_summary,
                            compensation_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?, ?)
                        """,
                        (
                            batch_id,
                            row["id"],
                            sequence,
                            mode,
                            str(entry.source),
                            str(entry.destination),
                            json.dumps(
                                {
                                    "size": row["source_size"],
                                    "mtime_ns": row["source_mtime_ns"],
                                    "file_index": row["source_file_index"],
                                }
                            ),
                            json.dumps(
                                {
                                    "size": record.get("result_size"),
                                    "mtime_ns": record.get("result_mtime_ns"),
                                }
                            ),
                            record.get("result_sha256"),
                            "rollback_failed"
                            if rollback_result.get("status") == "failed"
                            else None,
                            rollback_result.get("error"),
                            rollback_result.get("status"),
                        ),
                    )
                uow.connection.execute(
                    """
                    UPDATE operation_batches
                    SET status = ?, summary_json = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (
                        failure_status,
                        json.dumps(summary, ensure_ascii=False),
                        now_iso(),
                        batch_id,
                    ),
                )
                uow.connection.execute(
                    """
                    UPDATE plans SET status = ?
                    WHERE id = ? AND status = 'executing'
                    """,
                    (failure_status, plan_id),
                )
                uow.commit()
            raise
        log_records = []
        with log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    record = json.loads(line)
                    if record.get("applied"):
                        log_records.append(record)
        with SqliteUnitOfWork(self.database_path) as uow:
            for sequence, ((row, entry), record) in enumerate(zip(prepared, log_records), start=1):
                uow.connection.execute(
                    """
                    INSERT INTO operation_items(
                        batch_id, plan_item_id, sequence, action, source_path,
                        destination_path, source_identity_json, result_identity_json,
                        result_sha256, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'success')
                    """,
                    (
                        batch_id,
                        row["id"],
                        sequence,
                        mode,
                        str(entry.source),
                        str(entry.destination),
                        json.dumps(
                            {
                                "size": row["source_size"],
                                "mtime_ns": row["source_mtime_ns"],
                                "file_index": row["source_file_index"],
                            }
                        ),
                        json.dumps(
                            {
                                "size": record.get("result_size"),
                                "mtime_ns": record.get("result_mtime_ns"),
                            }
                        ),
                        record.get("result_sha256"),
                    ),
                )
                uow.connection.execute(
                    "UPDATE plan_items SET execution_status = 'completed' WHERE id = ?",
                    (row["id"],),
                )
            summary = {"log_path": str(log_path), "mode": mode, "item_count": len(log_records)}
            uow.connection.execute(
                """
                UPDATE operation_batches
                SET status = 'completed', summary_json = ?, finished_at = ? WHERE id = ?
                """,
                (json.dumps(summary, ensure_ascii=False), now_iso(), batch_id),
            )
            remaining = uow.connection.execute(
                """
                SELECT COUNT(*) FROM plan_items
                WHERE plan_id = ? AND action NOT IN ('skip', 'conflict')
                  AND execution_status != 'completed' AND decision IS NOT 'rejected'
                """,
                (plan_id,),
            ).fetchone()[0]
            # A partial run stays open so more items can be approved later;
            # a fully resolved plan is finished.
            final_status = "completed" if int(remaining) == 0 else prior_status
            uow.connection.execute(
                "UPDATE plans SET status = ? WHERE id = ? AND status = 'executing'",
                (final_status, plan_id),
            )
            touched = self._sync_library_entities(uow.connection, prepared)
            uow.commit()
        if touched:
            try:
                from autoanime_v3.services.metadata import MetadataEnrichmentService

                MetadataEnrichmentService(self.database_path).enrich_shows(touched)
            except Exception:
                pass  # 元数据补全失败绝不阻断文件整理
        facts = LibraryRepository(self.database_path)
        for row, entry in prepared:
            facts.observe_path(
                int(row["destination_root_id"]), entry.destination, "library", "video"
            )
        return self.get(batch_id)

    def _sync_library_entities(self, connection, rows_entries):
        """Create shows/seasons/episodes and media assignments in the Web database
        so the library page shows anime immediately after a successful execution.
        Returns [(show_id, canonical_title)] for the optional metadata enrichment pass."""
        touched = []
        for row, entry in rows_entries:
            resolution = entry.resolution
            title = (resolution.canonical_title or "").strip()
            media_file_id = row["media_file_id"]
            if not title or not media_file_id:
                continue
            key = alias_key(title)
            show = connection.execute(
                "SELECT id FROM shows WHERE normalized_key = ?", (key,)
            ).fetchone()
            locked = int(bool(getattr(resolution, "manual_lock", False)))
            if show is None:
                cursor = connection.execute(
                    "INSERT INTO shows(canonical_title, normalized_key, status, title_locked) VALUES (?, ?, 'active', ?)",
                    (title, key, locked),
                )
                show_id = int(cursor.lastrowid)
            else:
                show_id = int(show["id"])
                if locked:
                    connection.execute(
                        "UPDATE shows SET title_locked = 1 WHERE id = ? AND title_locked = 0",
                        (show_id,),
                    )
            touched.append((show_id, title))
            media_type = resolution.media_type or ("movie" if resolution.is_movie else "episode")
            if media_type == "movie" or resolution.is_movie:
                season_number = 1
                episode_number = "MOVIE"
                episode_type = "movie"
            elif media_type == "special":
                season_number = int(resolution.season) if resolution.season is not None else 0
                episode_number = str(resolution.episode) if resolution.episode is not None else "SP01"
                episode_type = "special"
            else:
                season_number = int(resolution.season) if resolution.season is not None else 1
                episode_number = str(resolution.episode) if resolution.episode is not None else str(season_number)
                episode_type = "episode"
            sort_value = int(episode_number) if episode_number.isdigit() else 0
            season = connection.execute(
                "SELECT id FROM seasons WHERE show_id = ? AND season_number = ?",
                (show_id, season_number),
            ).fetchone()
            if season is None:
                cursor = connection.execute(
                    "INSERT INTO seasons(show_id, season_number) VALUES (?, ?)",
                    (show_id, season_number),
                )
                season_id = int(cursor.lastrowid)
            else:
                season_id = int(season["id"])
            episode = connection.execute(
                """
                SELECT id FROM episodes
                WHERE season_id = ? AND episode_number = ? AND episode_type = ?
                """,
                (season_id, episode_number, episode_type),
            ).fetchone()
            if episode is None:
                cursor = connection.execute(
                    """
                    INSERT INTO episodes(season_id, episode_number, episode_type, sort_value)
                    VALUES (?, ?, ?, ?)
                    """,
                    (season_id, episode_number, episode_type, sort_value),
                )
                episode_id = int(cursor.lastrowid)
            else:
                episode_id = int(episode["id"])
            connection.execute(
                """
                INSERT INTO media_assignments(
                    media_file_id, show_id, season_id, episode_id, release_label, source
                ) VALUES (?, ?, ?, ?, ?, 'plan_execution')
                ON CONFLICT(media_file_id) DO UPDATE SET
                    show_id = excluded.show_id,
                    season_id = excluded.season_id,
                    episode_id = excluded.episode_id,
                    release_label = excluded.release_label,
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (media_file_id, show_id, season_id, episode_id, resolution.release_tag or None),
            )
        return touched

    def rollback(self, batch_id, requested_by=None):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            original = connection.execute(
                "SELECT kind, status FROM operation_batches WHERE id = ?", (batch_id,)
            ).fetchone()
        finally:
            connection.close()
        if original is not None and str(original["kind"]) == "correction":
            return self._rollback_correction(batch_id, requested_by)
        self.validate_rollback(batch_id)
        original, rollback_id, log_path = self._claim_rollback(batch_id, requested_by)
        try:
            with ResolutionCache(self.cache_path) as cache:
                restored = rollback_log(log_path, cache)
        except Exception as error:
            with SqliteUnitOfWork(self.database_path) as uow:
                uow.connection.execute(
                    """
                    UPDATE operation_batches
                    SET status = 'failed', summary_json = ?, finished_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (
                        json.dumps(
                            {"error": str(error), "source_log": str(log_path)},
                            ensure_ascii=False,
                        ),
                        now_iso(),
                        rollback_id,
                    ),
                )
                uow.commit()
            raise
        with SqliteUnitOfWork(self.database_path) as uow:
            uow.connection.execute(
                """
                UPDATE operation_batches
                SET status = 'completed', summary_json = ?, finished_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    json.dumps({"restored": restored, "source_log": str(log_path)}),
                    now_iso(),
                    rollback_id,
                ),
            )
            for item in original.items:
                uow.connection.execute(
                    """
                    INSERT INTO operation_items(
                        batch_id, sequence, action, source_path, destination_path,
                        source_identity_json, status, compensation_status
                    ) VALUES (?, ?, 'rollback', ?, ?, '{}', 'success', 'completed')
                    """,
                    (
                        rollback_id,
                        item.sequence,
                        item.destination_path,
                        item.source_path,
                    ),
                )
            uow.commit()
        return self.get(rollback_id)

    def _rollback_correction(self, batch_id, requested_by=None):
        with SqliteUnitOfWork(self.database_path) as uow:
            original = uow.connection.execute(
                "SELECT * FROM operation_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if original is None:
                raise NotFoundError("Operation batch does not exist", {"id": batch_id})
            if str(original["status"]) != "completed":
                raise InvalidStateError("Only a completed correction can be rolled back")
            existing = uow.connection.execute(
                """
                SELECT 1 FROM operation_batches
                WHERE parent_batch_id = ? AND kind = 'manual_rollback'
                LIMIT 1
                """,
                (batch_id,),
            ).fetchone()
            if existing is not None:
                raise InvalidStateError("Operation batch rollback was already claimed")
            cursor = uow.connection.execute(
                """
                INSERT INTO operation_batches(
                    plan_id, parent_batch_id, kind, status, requested_by, summary_json
                ) VALUES (NULL, ?, 'manual_rollback', 'running', ?, '{}')
                """,
                (batch_id, requested_by),
            )
            rollback_id = int(cursor.lastrowid)
            uow.commit()
        try:
            from autoanime_v3.services.corrections import CorrectionService

            CorrectionService(self.database_path, self.operation_dir).rollback(batch_id)
        except Exception as error:
            with SqliteUnitOfWork(self.database_path) as uow:
                uow.connection.execute(
                    """
                    UPDATE operation_batches
                    SET status = 'failed', summary_json = ?, finished_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (
                        json.dumps({"error": str(error)}, ensure_ascii=False),
                        now_iso(),
                        rollback_id,
                    ),
                )
                uow.commit()
            raise
        with SqliteUnitOfWork(self.database_path) as uow:
            uow.connection.execute(
                """
                UPDATE operation_batches
                SET status = 'completed', summary_json = ?, finished_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    json.dumps({"restored": True, "correction_batch": batch_id}, ensure_ascii=False),
                    now_iso(),
                    rollback_id,
                ),
            )
            uow.commit()
        return self.get(rollback_id)

    def _claim_rollback(self, batch_id, requested_by=None):
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = OperationRepository(uow.connection)
            original = repository.get(batch_id)
            if original is None:
                raise NotFoundError("Operation batch does not exist", {"id": batch_id})
            if original.status != "completed" or original.kind != "execute":
                raise InvalidStateError("Only a completed execution batch can be rolled back")
            existing = uow.connection.execute(
                """
                SELECT 1 FROM operation_batches
                WHERE parent_batch_id = ? AND kind = 'manual_rollback'
                LIMIT 1
                """,
                (batch_id,),
            ).fetchone()
            if existing is not None:
                raise InvalidStateError("Operation batch rollback was already claimed")
            log_path = Path(original.summary.get("log_path", ""))
            if not log_path.is_file():
                raise NotFoundError("Operation log is missing", {"path": str(log_path)})
            cursor = uow.connection.execute(
                """
                INSERT INTO operation_batches(
                    plan_id, parent_batch_id, kind, status, requested_by,
                    summary_json
                ) VALUES (?, ?, 'manual_rollback', 'running', ?, ?)
                """,
                (
                    original.plan_id,
                    original.id,
                    requested_by,
                    json.dumps({"source_log": str(log_path)}),
                ),
            )
            rollback_id = int(cursor.lastrowid)
            uow.commit()
            return original, rollback_id, log_path

    def validate_rollback(self, batch_id):
        original = self.get(batch_id)
        if original.status != "completed" or original.kind != "execute":
            raise InvalidStateError("Only a completed execution batch can be rolled back")
        connection = connect_sqlite(self.database_path)
        try:
            already_rolled_back = connection.execute(
                """
                SELECT 1 FROM operation_batches
                WHERE parent_batch_id = ? AND kind = 'manual_rollback'
                LIMIT 1
                """,
                (batch_id,),
            ).fetchone()
        finally:
            connection.close()
        if already_rolled_back is not None:
            raise InvalidStateError("Operation batch was already rolled back")
        log_path = Path(original.summary.get("log_path", ""))
        if not log_path.is_file():
            raise NotFoundError("Operation log is missing", {"path": str(log_path)})
        return original
