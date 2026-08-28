"""Auditable library corrections that move files on disk and merge mis-named shows.

When a user changes a show's canonical title in the library:
  * rename - the new title matches no other show: every library file of the show
             moves into the new <title> folder and file paths are updated.
  * merge  - the new title matches an existing show (same alias key): every
             library file of the source show moves into the target show's folder
             and the DB rows are re-parented to the target.
  * conflict - when both shows contain the same season+episode, the larger file
             wins and the smaller one is parked in a trash area (not deleted),
             so the whole correction stays rollbackable.

Corrections run through an operation batch (kind='correction') backed by a JSONL
log. The log header carries a full before-state snapshot of every affected DB row
plus the ordered file steps; rollback replays the file steps in reverse and
restores the database from the snapshot.
"""

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import NotFoundError, RevisionConflictError, ValidationError
from autoanime_v3.normalize import alias_key, safe_component
from autoanime_v3.path_safety import validate_library_destination
from autoanime_v3.services.changes import show_view
from autoanime_v3.services.memory import ShowMemoryService
from autoanime_v3.services.roots import normalize_windows_path


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _version_suffix(stem: str) -> str:
    match = re.search(r"(\s*\[[^\]]+\]\s*)+$", stem)
    return match.group(0).strip() if match else ""


def _episode_token(value) -> str:
    if isinstance(value, int):
        return "%02d" % value
    text = str(value)
    try:
        return "%02d" % int(text)
    except ValueError:
        return safe_component(text, 32)


def _identity_basename(season, episode, episode_type, title) -> str:
    """The episode identity without version markers, e.g. 'S01E01 - 标题'."""
    if episode_type == "movie":
        return safe_component(title)
    if episode_type == "special":
        token = _episode_token(episode)
        label = token if token.casefold().startswith("sp") else "SP" + token
        return "%s - %s" % (label, safe_component(title))
    return "S%02dE%s - %s" % (int(season or 1), _episode_token(episode), safe_component(title))


def _strip_version(stem: str) -> str:
    """Remove trailing [Group]/[version-...] markers to compare episode identity."""
    return re.sub(r"(\s*\[[^\]]+\]\s*)+$", "", stem)


def _build_basename(season, episode, episode_type, title, source_stem, ext) -> str:
    identity = _identity_basename(season, episode, episode_type, title)
    suffix = _version_suffix(source_stem)
    if not suffix:
        return identity + ext
    if suffix.startswith("["):
        return identity + " " + suffix + ext
    return identity + " [%s]" % suffix + ext


def _row_dict(row):
    return dict(row)


class CorrectionService:
    def __init__(self, database_path, operation_dir=None):
        self.database_path = Path(database_path)
        self.operation_dir = Path(operation_dir or self.database_path.parent / "operations")
        run_migrations(self.database_path)

    # ------------------------------------------------------------------ query

    def _connection(self):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        return connection

    def _show(self, connection, show_id):
        return connection.execute(
            "SELECT * FROM shows WHERE id = ?", (show_id,)
        ).fetchone()

    def _show_by_id(self, show_id):
        connection = self._connection()
        try:
            row = self._show(connection, show_id)
            if row is None:
                raise NotFoundError("Show does not exist", {"id": show_id})
            return row
        finally:
            connection.close()

    def impact(self, show_id, new_title):
        """Read-only preview of what a title change would do."""
        new_title = (new_title or "").strip()
        empty = {
            "merge": False,
            "target_show": None,
            "files_to_move": 0,
            "files_to_discard": 0,
            "files_missing": 0,
            "missing_paths": [],
        }
        if not new_title:
            return empty
        connection = self._connection()
        try:
            show = self._show(connection, show_id)
            if show is None:
                raise NotFoundError("Show does not exist", {"id": show_id})
            target = connection.execute(
                "SELECT * FROM shows WHERE normalized_key = ? AND id != ?",
                (alias_key(new_title), show_id),
            ).fetchone()
            assignments = self._assignments(connection, show_id)
        finally:
            connection.close()
        missing_paths = [
            str(Path(a["location_path"]))
            for a in assignments
            if not Path(a["location_path"]).exists()
        ]
        if not assignments or alias_key(new_title) == show["normalized_key"]:
            return {
                "merge": target is not None,
                "target_show": dict(target) if target is not None else None,
                "files_to_move": 0,
                "files_to_discard": 0,
                "files_missing": len(missing_paths),
                "missing_paths": missing_paths,
            }
        plan = self.build_plan(show, target, new_title, assignments)
        missing_set = set(missing_paths)
        return {
            "merge": plan["merge"],
            "target_show": dict(target) if target is not None else None,
            "files_to_move": sum(
                1
                for s in plan["steps"]
                if s["op"] == "move" and s.get("from_path") not in missing_set
            ),
            "files_to_discard": sum(1 for s in plan["steps"] if s["op"] == "trash"),
            "files_missing": len(missing_paths),
            "missing_paths": missing_paths,
        }

    def _assignments(self, connection, show_id):
        return connection.execute(
            """
            SELECT ma.id AS assignment_id, ma.media_file_id, ma.release_label,
                   ma.season_id AS source_season_id, ma.episode_id AS source_episode_id,
                   fl.id AS location_id, fl.root_id, fl.path AS location_path,
                   sr.path AS root_path,
                   s.season_number, e.episode_number, e.episode_type
            FROM media_assignments ma
            JOIN file_locations fl ON fl.media_file_id = ma.media_file_id
                 AND fl.role = 'library' AND fl.state = 'present'
            JOIN storage_roots sr ON sr.id = fl.root_id
            LEFT JOIN seasons s ON s.id = ma.season_id
            LEFT JOIN episodes e ON e.id = ma.episode_id
            WHERE ma.show_id = ?
            ORDER BY fl.id
            """,
            (show_id,),
        ).fetchall()

    def _file_owner(self, connection, path):
        """media/assignment/location owning a library file at an exact path."""
        return connection.execute(
            """
            SELECT ma.id AS assignment_id, ma.media_file_id, fl.id AS location_id
            FROM file_locations fl
            JOIN media_assignments ma ON ma.media_file_id = fl.media_file_id
            WHERE fl.path = ? AND fl.role = 'library' AND fl.state = 'present'
            LIMIT 1
            """,
            (str(path),),
        ).fetchone()

    # ------------------------------------------------------------ plan build

    def build_plan(self, show, target, new_title, assignments):
        """Return ordered file steps and the per-assignment outcome."""
        merge = target is not None
        target_title = target["canonical_title"] if merge else new_title

        steps = []
        decisions = {}  # source assignment_id -> "survivor" | "discard"
        target_discards = []  # target files displaced by a larger source file
        discarded_paths = []
        connection = self._connection()
        try:
            for a in assignments:
                root_path = Path(a["root_path"])
                location_path = Path(a["location_path"])
                season = int(a["season_number"] or 1)
                episode = a["episode_number"]
                etype = a["episode_type"] or "episode"
                corrected_title = target_title if merge else new_title
                destination = self._destination(
                    root_path, corrected_title, season, episode, etype, location_path
                )
                if destination == location_path:
                    continue
                validate_library_destination(root_path, destination)
                if not location_path.exists():
                    steps.append(
                        {
                            "op": "move",
                            "assignment_id": int(a["assignment_id"]),
                            "media_file_id": int(a["media_file_id"]),
                            "location_id": int(a["location_id"]),
                            "from_path": str(location_path),
                            "to_path": str(destination),
                            "survivor": True,
                        }
                    )
                    decisions[int(a["assignment_id"])] = "survivor"
                    continue
                identity = _identity_basename(season, episode, etype, corrected_title)
                existing = self._find_collision(
                    destination, identity, location_path.suffix.lower(), location_path
                )
                if existing is not None:
                    owner = self._file_owner(connection, existing)
                    if owner is None:
                        raise ValidationError(
                            "Destination is occupied by an untracked file",
                            {"path": str(existing)},
                        )
                    source_size = location_path.stat().st_size
                    destination_size = existing.stat().st_size
                    if source_size > destination_size:
                        steps.append(
                            {
                                "op": "trash",
                                "kind": "target",
                                "assignment_id": int(owner["assignment_id"]),
                                "media_file_id": int(owner["media_file_id"]),
                                "location_id": int(owner["location_id"]),
                                "from_path": str(existing),
                            }
                        )
                        target_discards.append({"assignment_id": int(owner["assignment_id"])})
                        steps.append(
                            {
                                "op": "move",
                                "assignment_id": int(a["assignment_id"]),
                                "media_file_id": int(a["media_file_id"]),
                                "location_id": int(a["location_id"]),
                                "from_path": str(location_path),
                                "to_path": str(destination),
                                "survivor": True,
                            }
                        )
                        decisions[int(a["assignment_id"])] = "survivor"
                        discarded_paths.append(str(existing))
                    else:
                        steps.append(
                            {
                                "op": "trash",
                                "kind": "source",
                                "assignment_id": int(a["assignment_id"]),
                                "media_file_id": int(a["media_file_id"]),
                                "location_id": int(a["location_id"]),
                                "from_path": str(location_path),
                            }
                        )
                        decisions[int(a["assignment_id"])] = "discard"
                        discarded_paths.append(str(location_path))
                else:
                    steps.append(
                        {
                            "op": "move",
                            "assignment_id": int(a["assignment_id"]),
                            "media_file_id": int(a["media_file_id"]),
                            "location_id": int(a["location_id"]),
                            "from_path": str(location_path),
                            "to_path": str(destination),
                            "survivor": True,
                        }
                    )
                    decisions[int(a["assignment_id"])] = "survivor"
        finally:
            connection.close()
        return {
            "merge": merge,
            "target_show_id": target["id"] if merge else None,
            "steps": steps,
            "decisions": decisions,
            "target_discards": target_discards,
            "discarded_paths": discarded_paths,
        }

    def _destination(self, root_path, corrected_title, season, episode, etype, location_path):
        folder = safe_component(corrected_title)
        if etype == "movie":
            directory = root_path / folder
        elif etype == "special":
            directory = root_path / folder / "Specials"
        else:
            directory = root_path / folder / ("Season %02d" % int(season or 1))
        return directory / _build_basename(
            season, episode, etype, corrected_title, location_path.stem, location_path.suffix.lower()
        )

    def _find_collision(self, destination, identity, ext, location_path):
        """A file at `destination`, or in the same directory with the same episode
        identity, that would collide with the incoming file."""
        if destination.exists():
            return destination
        parent = destination.parent
        if not parent.is_dir():
            return None
        for candidate in parent.glob("*" + ext):
            if candidate == location_path:
                continue
            if _strip_version(candidate.stem) == identity:
                return candidate
        return None

    # -------------------------------------------------------------- applying

    def apply(self, request_id, requested_by=None):
        with SqliteUnitOfWork(self.database_path) as uow:
            request = uow.connection.execute(
                "SELECT * FROM change_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise NotFoundError("Change request does not exist", {"id": request_id})
            if request["status"] != "validated":
                raise ValidationError("Change request is not in a validated state")
            show = self._show(uow.connection, request["target_id"])
            if show is None:
                raise NotFoundError("Show does not exist")
            if int(show["revision"]) != int(request["base_revision"]):
                raise RevisionConflictError("Show changed before applying the request")
            patch = json.loads(request["patch_json"])
            new_title = str(patch.get("canonical_title") or "").strip()
            if not new_title:
                raise ValidationError("A canonical title is required")
            target = uow.connection.execute(
                "SELECT * FROM shows WHERE normalized_key = ? AND id != ?",
                (alias_key(new_title), show["id"]),
            ).fetchone()
            assignments = self._assignments(uow.connection, show["id"])

        if not assignments:
            if target is not None:
                result = self._apply_empty_merge(request_id, show, target)
                self._remember_correction(show, result.canonical_title if hasattr(result, "canonical_title") else target["canonical_title"], patch)
                return result
            result = self._apply_title_only(request_id, new_title, patch)
            self._remember_correction(show, new_title, patch)
            return result

        plan = self.build_plan(show, target, new_title, assignments)
        missing = [
            step["from_path"]
            for step in plan["steps"]
            if step.get("from_path") and not Path(step["from_path"]).exists()
        ]
        if missing:
            raise ValidationError("Source file is missing", {"path": missing[0], "count": len(missing)})
        if not plan["steps"]:
            result = self._apply_title_only(request_id, new_title, patch)
            self._remember_correction(show, new_title, patch)
            return result
        target_show_id = plan["target_show_id"] or show["id"]
        self._execute(request_id, show, target, new_title, patch, plan, requested_by)
        result = show_view(self._show_by_id(target_show_id))
        self._remember_correction(show, new_title, patch)
        return result

    def _remember_correction(self, show, new_title, patch):
        aliases = [
            new_title,
            show["canonical_title"] if show is not None else "",
            show["normalized_key"] if show is not None else "",
        ]
        extra = patch.get("aliases") if isinstance(patch, dict) else None
        if extra:
            aliases.extend(list(extra))
        ShowMemoryService(self.database_path).remember(
            aliases, new_title, source="library_correction", confidence=100
        )

    def _apply_empty_merge(self, request_id, show, target):
        """An empty duplicate show merged into an existing one: just drop it."""
        with SqliteUnitOfWork(self.database_path) as uow:
            uow.connection.execute(
                "UPDATE change_requests SET status = 'applied', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (request_id,),
            )
            uow.connection.execute("DELETE FROM shows WHERE id = ?", (show["id"],))
            uow.commit()
        return show_view(self._show_by_id(target["id"]))

    def _apply_title_only(self, request_id, new_title, patch):
        locked = int(bool(patch.get("title_locked", False)))
        with SqliteUnitOfWork(self.database_path) as uow:
            request = uow.connection.execute(
                "SELECT * FROM change_requests WHERE id = ?", (request_id,)
            ).fetchone()
            show = self._show(uow.connection, request["target_id"])
            uow.connection.execute(
                """
                UPDATE shows SET canonical_title = ?, normalized_key = ?, title_locked = ?,
                    revision = revision + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (new_title, alias_key(new_title), locked, show["id"]),
            )
            uow.connection.execute(
                "UPDATE change_requests SET status = 'applied', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (request_id,),
            )
            uow.commit()
        return show_view(self._show_by_id(show["id"]))

    def _execute(self, request_id, show, target, new_title, patch, plan, requested_by=None):
        batch_id = None
        trash_dir = None
        log_path = None
        with SqliteUnitOfWork(self.database_path) as uow:
            self.operation_dir.mkdir(parents=True, exist_ok=True)
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            log_path = self.operation_dir / (run_id + ".jsonl")
            cursor = uow.connection.execute(
                """
                INSERT INTO operation_batches(plan_id, kind, status, requested_by, summary_json)
                VALUES (NULL, 'correction', 'running', ?, ?)
                """,
                (requested_by, json.dumps({"log_path": str(log_path)}, ensure_ascii=False)),
            )
            batch_id = int(cursor.lastrowid)
            trash_dir = self.operation_dir / "trash" / ("corr-%d" % batch_id)
            trash_dir.mkdir(parents=True, exist_ok=True)
            uow.connection.execute(
                "UPDATE change_requests SET status = 'applying', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (request_id,),
            )
            uow.commit()

        header = self._snapshot(show, target, plan, new_title, batch_id, log_path)
        applied = []
        try:
            with log_path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(header, ensure_ascii=False) + "\n")
                for step in plan["steps"]:
                    self._apply_step(step, trash_dir)
                    applied.append(dict(step))
                    handle.write(json.dumps(step, ensure_ascii=False) + "\n")
        except Exception as error:
            self._reverse_steps(applied, trash_dir)
            with SqliteUnitOfWork(self.database_path) as uow:
                uow.connection.execute(
                    """
                    UPDATE operation_batches
                    SET status = 'failed', summary_json = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps({"error": str(error), "log_path": str(log_path)}, ensure_ascii=False),
                        now_iso(),
                        batch_id,
                    ),
                )
                uow.connection.execute(
                    "UPDATE change_requests SET status = 'validated', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (request_id,),
                )
                uow.commit()
            if isinstance(error, ValidationError):
                raise
            if isinstance(error, FileNotFoundError):
                raise ValidationError(
                    "Source file is missing",
                    {"path": str(getattr(error, "filename", None) or "")},
                ) from error
            if isinstance(error, OSError):
                raise ValidationError(
                    "Source file disappeared before execution",
                    {"error": str(error)},
                ) from error
            raise

        created = self._apply_database(request_id, show, target, new_title, patch, plan, batch_id, log_path)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"db_applied": True, "created": created}, ensure_ascii=False) + "\n")

    def _snapshot(self, show, target, plan, new_title, batch_id, log_path):
        connection = self._connection()
        try:
            show_id = show["id"]
            seasons = [dict(r) for r in connection.execute(
                "SELECT id, show_id, season_number FROM seasons WHERE show_id = ?", (show_id,)
            ).fetchall()]
            episodes = [dict(r) for r in connection.execute(
                """
                SELECT e.id, e.season_id, e.episode_number, e.episode_type
                FROM episodes e JOIN seasons s ON s.id = e.season_id
                WHERE s.show_id = ?
                """,
                (show_id,),
            ).fetchall()]
            assignment_ids = {int(s.get("assignment_id")) for s in plan["steps"] if s.get("assignment_id")}
            for d in plan["target_discards"]:
                assignment_ids.add(int(d["assignment_id"]))
            assignments = []
            for aid in assignment_ids:
                row = connection.execute(
                    "SELECT id, media_file_id, show_id, season_id, episode_id, release_label, source FROM media_assignments WHERE id = ?",
                    (aid,),
                ).fetchone()
                if row is not None:
                    assignments.append(dict(row))
            location_ids = {int(s["location_id"]) for s in plan["steps"]}
            locations = []
            for lid in location_ids:
                row = connection.execute(
                    "SELECT id, media_file_id, root_id, path, normalized_path, role, state FROM file_locations WHERE id = ?",
                    (lid,),
                ).fetchone()
                if row is not None:
                    locations.append(dict(row))
            show_snapshot = dict(connection.execute(
                "SELECT id, canonical_title, normalized_key, status, title_locked, revision FROM shows WHERE id = ?",
                (show_id,),
            ).fetchone())
        finally:
            connection.close()
        return {
            "kind": "correction",
            "batch_id": batch_id,
            "log_path": str(log_path),
            "merge": plan["merge"],
            "show_id": show_id,
            "target_show_id": plan["target_show_id"],
            "old_title": show["canonical_title"],
            "new_title": new_title,
            "old_key": show["normalized_key"],
            "old_title_locked": bool(show["title_locked"]),
            "snapshot": {
                "show": show_snapshot,
                "seasons": seasons,
                "episodes": episodes,
                "assignments": assignments,
                "locations": locations,
            },
        }

    def _apply_step(self, step, trash_dir):
        if step["op"] == "trash":
            from_path = Path(step["from_path"])
            to_path = trash_dir / from_path.name
            step["to_path"] = str(to_path)
            if from_path.exists():
                to_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(from_path), str(to_path))
                step["result"] = True
            else:
                step["result"] = False
                step["missing"] = True
        else:
            from_path = Path(step["from_path"])
            to_path = Path(step["to_path"])
            if not from_path.exists():
                raise ValidationError("Source file is missing", {"path": str(from_path)})
            to_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(from_path), str(to_path))
            step["result"] = True

    def _reverse_steps(self, steps, trash_dir):
        for step in reversed(steps):
            try:
                if step["op"] == "trash" and step.get("result"):
                    if Path(step["to_path"]).exists():
                        Path(step["from_path"]).parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(step["to_path"], step["from_path"])
                elif step["op"] == "move":
                    if Path(step["to_path"]).exists():
                        Path(step["from_path"]).parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(step["to_path"], step["from_path"])
            except OSError:
                continue

    def _apply_database(self, request_id, show, target, new_title, patch, plan, batch_id, log_path):
        locked = int(bool(patch.get("title_locked", False)))
        created = {"season_ids": [], "episode_ids": []}
        connection = self._connection()
        try:
            # DB re-parenting first (it reads source assignments joined to the
            # still-present library locations), then update the moved paths.
            if not plan["merge"]:
                connection.execute(
                    """
                    UPDATE shows SET canonical_title = ?, normalized_key = ?, title_locked = ?,
                        revision = revision + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                    """,
                    (new_title, alias_key(new_title), locked, show["id"]),
                )
            else:
                self._reparent_to_target(connection, show, target, plan, created)
            for step in plan["steps"]:
                location_id = int(step["location_id"])
                if step["op"] == "trash":
                    connection.execute(
                        "UPDATE file_locations SET state = 'removed', last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (location_id,),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE file_locations SET path = ?, normalized_path = ?, last_seen_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (step["to_path"], normalize_windows_path(step["to_path"]), location_id),
                    )
            connection.execute(
                "UPDATE change_requests SET status = 'applied', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (request_id,),
            )
            connection.execute(
                """
                UPDATE operation_batches
                SET status = 'completed', summary_json = ?, finished_at = ? WHERE id = ?
                """,
                (
                    json.dumps(
                        {
                            "log_path": str(log_path),
                            "kind": "correction",
                            "merge": plan["merge"],
                            "moved": sum(1 for s in plan["steps"] if s["op"] == "move"),
                            "discarded": plan["discarded_paths"],
                        },
                        ensure_ascii=False,
                    ),
                    now_iso(),
                    batch_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return created

    def _reparent_to_target(self, connection, show, target, plan, created):
        target_id = target["id"]
        season_map = {}
        for row in connection.execute(
            "SELECT id, season_number FROM seasons WHERE show_id = ?", (target_id,)
        ).fetchall():
            season_map[int(row["season_number"])] = int(row["id"])
        episode_map = {}
        for row in connection.execute(
            """
            SELECT e.id, e.season_id, e.episode_number, e.episode_type
            FROM episodes e JOIN seasons s ON s.id = e.season_id
            WHERE s.show_id = ?
            """,
            (target_id,),
        ).fetchall():
            episode_map[(int(row["season_id"]), row["episode_number"], row["episode_type"])] = int(row["id"])

        source_assignments = self._assignments(connection, show["id"])
        for a in source_assignments:
            decision = plan["decisions"].get(int(a["assignment_id"]), "survivor")
            season_number = int(a["season_number"] or 1)
            episode = a["episode_number"]
            etype = a["episode_type"] or "episode"
            source_season_id = int(a["source_season_id"]) if a["source_season_id"] else None
            source_episode_id = int(a["source_episode_id"]) if a["source_episode_id"] else None
            if decision == "discard":
                connection.execute(
                    "DELETE FROM media_assignments WHERE id = ?", (a["assignment_id"],)
                )
                continue
            season_id = season_map.get(season_number)
            if season_id is None:
                if source_season_id is not None:
                    connection.execute(
                        "UPDATE seasons SET show_id = ? WHERE id = ?", (target_id, source_season_id)
                    )
                    season_id = source_season_id
                else:
                    cursor = connection.execute(
                        "INSERT INTO seasons(show_id, season_number) VALUES (?, ?)",
                        (target_id, season_number),
                    )
                    season_id = int(cursor.lastrowid)
                    created["season_ids"].append(season_id)
                season_map[season_number] = season_id
            episode_key = (season_id, episode, etype)
            episode_id = episode_map.get(episode_key)
            if episode_id is None:
                if source_episode_id is not None:
                    connection.execute(
                        "UPDATE episodes SET season_id = ? WHERE id = ?", (season_id, source_episode_id)
                    )
                    episode_id = source_episode_id
                else:
                    cursor = connection.execute(
                        """
                        INSERT INTO episodes(season_id, episode_number, episode_type, sort_value)
                        VALUES (?, ?, ?, ?)
                        """,
                        (season_id, episode, etype, int(episode) if str(episode).isdigit() else 0),
                    )
                    episode_id = int(cursor.lastrowid)
                    created["episode_ids"].append(episode_id)
                episode_map[episode_key] = episode_id
            else:
                # Target already owns this episode: the source episode row is a duplicate.
                if source_episode_id is not None and source_episode_id != episode_id:
                    connection.execute("DELETE FROM episodes WHERE id = ?", (source_episode_id,))
            connection.execute(
                """
                UPDATE media_assignments SET show_id = ?, season_id = ?, episode_id = ? WHERE id = ?
                """,
                (target_id, season_id, episode_id, a["assignment_id"]),
            )

        # Target's own (smaller) file lost the conflict: drop its assignment.
        for d in plan["target_discards"]:
            connection.execute(
                "DELETE FROM media_assignments WHERE id = ?", (d["assignment_id"],)
            )

        # Orphaned source seasons/episodes that no longer hold any assignment.
        for sid in {int(a["source_season_id"]) for a in source_assignments if a["source_season_id"]}:
            remaining = connection.execute(
                "SELECT COUNT(*) FROM media_assignments WHERE show_id = ? AND season_id = ?",
                (show["id"], sid),
            ).fetchone()[0]
            if int(remaining) == 0:
                connection.execute("DELETE FROM seasons WHERE id = ? AND show_id = ?", (sid, show["id"]))
        connection.execute("DELETE FROM shows WHERE id = ?", (show["id"],))

    # -------------------------------------------------------------- backfill

    def backfill_library(self):
        """Populate shows/seasons/episodes/media_assignments for executions that
        completed before shows-sync existed. Idempotent: media that already has
        an assignment is skipped."""
        connection = self._connection()
        try:
            rows = connection.execute(
                """
                SELECT pi.id, pi.destination_root_id, pi.destination_relative_path,
                       pi.identification_snapshot_json, sr.path AS root_path
                FROM plan_items pi
                JOIN storage_roots sr ON sr.id = pi.destination_root_id
                WHERE pi.execution_status = 'completed'
                ORDER BY pi.id
                """
            ).fetchall()
            created = 0
            for row in rows:
                snapshot = json.loads(row["identification_snapshot_json"])
                title = str(snapshot.get("title") or "").strip()
                if not title:
                    continue
                destination = Path(row["root_path"]) / row["destination_relative_path"]
                media_file_id = connection.execute(
                    """
                    SELECT media_file_id FROM file_locations
                    WHERE path = ? AND role = 'library' AND state = 'present'
                    LIMIT 1
                    """,
                    (str(destination),),
                ).fetchone()
                if media_file_id is None:
                    continue
                media_file_id = int(media_file_id["media_file_id"])
                if connection.execute(
                    "SELECT 1 FROM media_assignments WHERE media_file_id = ? LIMIT 1",
                    (media_file_id,),
                ).fetchone() is not None:
                    continue
                self._upsert_entities(
                    connection,
                    media_file_id,
                    title,
                    snapshot.get("season"),
                    snapshot.get("episode"),
                    bool(snapshot.get("is_movie", False)),
                    str(snapshot.get("release_tag") or "") or None,
                )
                created += 1
            connection.commit()
            return created
        finally:
            connection.close()

    def _upsert_entities(self, connection, media_file_id, title, season, episode,
                         is_movie, release_tag):
        key = alias_key(title)
        show = connection.execute(
            "SELECT id FROM shows WHERE normalized_key = ?", (key,)
        ).fetchone()
        if show is None:
            cursor = connection.execute(
                "INSERT INTO shows(canonical_title, normalized_key, status) VALUES (?, ?, 'active')",
                (title, key),
            )
            show_id = int(cursor.lastrowid)
        else:
            show_id = int(show["id"])
        if is_movie:
            season_number = 1
            episode_number = "MOVIE"
            episode_type = "movie"
        else:
            season_number = int(season) if season is not None else 1
            episode_number = str(episode) if episode is not None else str(season_number)
            episode_type = "episode"
        sort_value = int(episode_number) if episode_number.isdigit() else 0
        season_row = connection.execute(
            "SELECT id FROM seasons WHERE show_id = ? AND season_number = ?",
            (show_id, season_number),
        ).fetchone()
        if season_row is None:
            cursor = connection.execute(
                "INSERT INTO seasons(show_id, season_number) VALUES (?, ?)",
                (show_id, season_number),
            )
            season_id = int(cursor.lastrowid)
        else:
            season_id = int(season_row["id"])
        episode_row = connection.execute(
            """
            SELECT id FROM episodes
            WHERE season_id = ? AND episode_number = ? AND episode_type = ?
            """,
            (season_id, episode_number, episode_type),
        ).fetchone()
        if episode_row is None:
            cursor = connection.execute(
                """
                INSERT INTO episodes(season_id, episode_number, episode_type, sort_value)
                VALUES (?, ?, ?, ?)
                """,
                (season_id, episode_number, episode_type, sort_value),
            )
            episode_id = int(cursor.lastrowid)
        else:
            episode_id = int(episode_row["id"])
        connection.execute(
            """
            INSERT INTO media_assignments(
                media_file_id, show_id, season_id, episode_id, release_label, source
            ) VALUES (?, ?, ?, ?, ?, 'plan_execution')
            ON CONFLICT(media_file_id) DO UPDATE SET
                show_id = excluded.show_id, season_id = excluded.season_id,
                episode_id = excluded.episode_id, release_label = excluded.release_label,
                source = excluded.source, updated_at = CURRENT_TIMESTAMP
            """,
            (media_file_id, show_id, season_id, episode_id, release_tag),
        )

    # -------------------------------------------------------------- rollback

    def rollback(self, batch_id):
        connection = self._connection()
        try:
            batch = connection.execute(
                "SELECT * FROM operation_batches WHERE id = ?", (batch_id,)
            ).fetchone()
        finally:
            connection.close()
        if batch is None:
            raise NotFoundError("Operation batch does not exist", {"id": batch_id})
        summary = json.loads(batch["summary_json"] or "{}")
        log_path = Path(summary.get("log_path") or "")
        if not log_path.is_file():
            raise NotFoundError("Correction log is missing", {"path": str(log_path)})
        records = []
        with log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
        header = records[0]
        steps = [r for r in records[1:] if "op" in r]
        created = {"season_ids": [], "episode_ids": []}
        for record in records[1:]:
            if "created" in record:
                created = record.get("created") or created
        trash_dir = self.operation_dir / "trash" / ("corr-%d" % batch_id)
        self._reverse_steps(steps, trash_dir)
        self._restore_database(header, created)
        return {"status": "rolled_back", "batch_id": batch_id}

    def _restore_database(self, header, created):
        snapshot = header["snapshot"]
        show_id = int(header["show_id"])
        connection = self._connection()
        try:
            if header.get("merge"):
                # Remove seasons/episodes created on the target side during the merge.
                for eid in created.get("episode_ids", []):
                    connection.execute("DELETE FROM episodes WHERE id = ?", (eid,))
                for sid in created.get("season_ids", []):
                    connection.execute("DELETE FROM seasons WHERE id = ?", (sid,))
                # Recreate the source show row.
                connection.execute(
                    """
                    INSERT OR IGNORE INTO shows(id, canonical_title, normalized_key, status, title_locked, revision)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        show_id,
                        snapshot["show"]["canonical_title"],
                        snapshot["show"]["normalized_key"],
                        snapshot["show"]["status"],
                        int(snapshot["show"]["title_locked"]),
                        snapshot["show"]["revision"],
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE shows SET canonical_title = ?, normalized_key = ?,
                        title_locked = ?, revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        header["old_title"],
                        header["old_key"],
                        int(header["old_title_locked"]),
                        show_id,
                    ),
                )
            # Restore seasons, episodes, assignments and locations to their
            # before-state (rows may have been re-parented or deleted).
            for row in snapshot["seasons"]:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO seasons(id, show_id, season_number) VALUES (?, ?, ?)
                    """,
                    (row["id"], row["show_id"], row["season_number"]),
                )
            for row in snapshot["episodes"]:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO episodes(id, season_id, episode_number, episode_type, sort_value)
                    VALUES (?, ?, ?, ?, 0)
                    """,
                    (row["id"], row["season_id"], row["episode_number"], row["episode_type"]),
                )
            for row in snapshot["assignments"]:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO media_assignments(
                        id, media_file_id, show_id, season_id, episode_id, release_label, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["media_file_id"],
                        row["show_id"],
                        row["season_id"],
                        row["episode_id"],
                        row.get("release_label"),
                        row.get("source") or "plan_execution",
                    ),
                )
            for row in snapshot["locations"]:
                connection.execute(
                    """
                    UPDATE file_locations SET path = ?, normalized_path = ?, state = ?,
                        last_seen_at = CURRENT_TIMESTAMP WHERE id = ?
                    """,
                    (
                        row["path"],
                        row["normalized_path"],
                        "present" if row["role"] == "library" else row["state"],
                        row["id"],
                    ),
                )
            connection.commit()
        finally:
            connection.close()
