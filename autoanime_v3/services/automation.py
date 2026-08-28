"""Persistent automation producers for schedules, downloader hooks, and watchdog events."""

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.repositories.jobs import JobRepository
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import (
    NotFoundError,
    PathOutsideRootError,
    RevisionConflictError,
    ValidationError,
)
from autoanime_v3.jobs.queue import JobQueue
from autoanime_v3.jobs.watcher import StableFileBuffer
from autoanime_v3.services.roots import normalize_windows_path, path_is_within


def utc_now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def parse_iso(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _zone(timezone_name):
    if timezone_name == "UTC":
        return timezone.utc
    if timezone_name == "Asia/Shanghai":
        return timezone(timedelta(hours=8), "Asia/Shanghai")
    return ZoneInfo(timezone_name)


@dataclass(frozen=True)
class Schedule:
    id: int
    profile_id: int
    kind: str
    schedule: dict
    timezone: str
    next_run_at: str | None
    last_run_at: str | None
    enabled: bool
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WebhookSource:
    id: int
    name: str
    downloader: str
    profile_id: int
    enabled: bool
    last_called_at: str | None
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CreatedWebhookSource(WebhookSource):
    token: str


def _schedule_from_row(row):
    return Schedule(
        id=int(row["id"]),
        profile_id=int(row["profile_id"]),
        kind=str(row["kind"]),
        schedule=json.loads(row["schedule_json"]),
        timezone=str(row["timezone"]),
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
        enabled=bool(row["enabled"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _webhook_from_row(row):
    return WebhookSource(
        id=int(row["id"]),
        name=str(row["name"]),
        downloader=str(row["downloader"]),
        profile_id=int(row["profile_id"]),
        enabled=bool(row["enabled"]),
        last_called_at=row["last_called_at"],
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _validate_schedule(kind, schedule, timezone_name):
    try:
        _zone(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValidationError("Unknown schedule timezone", {"timezone": timezone_name}) from error
    if kind == "interval":
        minutes = schedule.get("interval_minutes")
        if type(minutes) is not int or minutes < 1:
            raise ValidationError("interval_minutes must be an integer greater than zero")
        return {"interval_minutes": minutes}
    if kind == "daily":
        value = schedule.get("time")
        try:
            hour_text, minute_text = str(value).split(":")
            hour, minute = int(hour_text), int(minute_text)
        except (TypeError, ValueError) as error:
            raise ValidationError("Daily schedule time must use HH:MM") from error
        if not (0 <= hour <= 23 and 0 <= minute <= 59) or str(value) != f"{hour:02d}:{minute:02d}":
            raise ValidationError("Daily schedule time must use HH:MM")
        return {"time": value}
    raise ValidationError("Unsupported schedule kind", {"kind": kind})


def next_run(kind, schedule, timezone_name, after):
    if kind == "interval":
        return after.astimezone(timezone.utc) + timedelta(minutes=schedule["interval_minutes"])
    zone = _zone(timezone_name)
    local_after = after.astimezone(zone)
    hour, minute = (int(part) for part in schedule["time"].split(":"))
    candidate = local_after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_after:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


class ScheduleService:
    def __init__(self, database_path, clock=None):
        self.database_path = Path(database_path)
        self.clock = clock or utc_now
        run_migrations(self.database_path)

    def create(self, profile_id, kind, schedule, timezone_name="UTC", enabled=True):
        document = _validate_schedule(kind, schedule, timezone_name)
        now = self.clock()
        with SqliteUnitOfWork(self.database_path) as uow:
            if uow.connection.execute(
                "SELECT 1 FROM scan_profiles WHERE id = ?", (profile_id,)
            ).fetchone() is None:
                raise NotFoundError("Scan profile does not exist", {"id": profile_id})
            upcoming = iso(next_run(kind, document, timezone_name, now)) if enabled else None
            cursor = uow.connection.execute(
                """
                INSERT INTO schedules(profile_id, kind, schedule_json, timezone, next_run_at, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (profile_id, kind, json.dumps(document), timezone_name, upcoming, int(enabled)),
            )
            row = uow.connection.execute(
                "SELECT * FROM schedules WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            uow.commit()
            return _schedule_from_row(row)

    def list(self):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            return tuple(
                _schedule_from_row(row)
                for row in connection.execute(
                    """
                    SELECT s.*
                    FROM schedules s
                    JOIN scan_profiles p ON p.id = s.profile_id
                    WHERE p.deleted_at IS NULL
                    ORDER BY s.id
                    """
                )
            )
        finally:
            connection.close()

    def get(self, schedule_id):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            row = connection.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise NotFoundError("Schedule does not exist", {"id": schedule_id})
        return _schedule_from_row(row)

    def update(self, schedule_id, revision, patch):
        allowed = {"profile_id", "kind", "schedule", "timezone", "enabled"}
        if not patch or set(patch) - allowed:
            raise ValidationError("Unsupported or empty schedule update")
        with SqliteUnitOfWork(self.database_path) as uow:
            row = uow.connection.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
            if row is None:
                raise NotFoundError("Schedule does not exist", {"id": schedule_id})
            if int(row["revision"]) != int(revision):
                raise RevisionConflictError("Schedule revision is stale")
            profile_id = int(patch.get("profile_id", row["profile_id"]))
            kind = str(patch.get("kind", row["kind"]))
            document = patch.get("schedule", json.loads(row["schedule_json"]))
            timezone_name = str(patch.get("timezone", row["timezone"]))
            enabled = patch.get("enabled", bool(row["enabled"]))
            if type(enabled) is not bool:
                raise ValidationError("Schedule enabled state must be true or false")
            document = _validate_schedule(kind, document, timezone_name)
            if uow.connection.execute("SELECT 1 FROM scan_profiles WHERE id = ?", (profile_id,)).fetchone() is None:
                raise NotFoundError("Scan profile does not exist", {"id": profile_id})
            upcoming = iso(next_run(kind, document, timezone_name, self.clock())) if enabled else None
            uow.connection.execute(
                """
                UPDATE schedules SET profile_id = ?, kind = ?, schedule_json = ?, timezone = ?,
                    enabled = ?, next_run_at = ?, revision = revision + 1,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (profile_id, kind, json.dumps(document), timezone_name, int(enabled), upcoming, schedule_id),
            )
            result = uow.connection.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
            uow.commit()
            return _schedule_from_row(result)

    def delete(self, schedule_id, revision):
        with SqliteUnitOfWork(self.database_path) as uow:
            row = uow.connection.execute("SELECT revision FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
            if row is None:
                raise NotFoundError("Schedule does not exist", {"id": schedule_id})
            if int(row["revision"]) != int(revision):
                raise RevisionConflictError("Schedule revision is stale")
            uow.connection.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            uow.commit()

    def enqueue_due(self):
        now = self.clock().astimezone(timezone.utc)
        produced = []
        with SqliteUnitOfWork(self.database_path) as uow:
            rows = uow.connection.execute(
                """
                SELECT s.* FROM schedules s JOIN scan_profiles p ON p.id = s.profile_id
                WHERE s.enabled = 1 AND p.enabled = 1 AND s.next_run_at IS NOT NULL
                  AND s.next_run_at <= ? ORDER BY s.next_run_at, s.id
                """,
                (iso(now),),
            ).fetchall()
            jobs = JobRepository(uow.connection)
            for row in rows:
                occurrence = str(row["next_run_at"])
                key = f"schedule:{row['id']}:{occurrence}"
                job = jobs.find_by_idempotency_key(key)
                if job is None:
                    job = jobs.enqueue(
                        "scan",
                        {"profile_id": int(row["profile_id"]), "paths": [], "trigger": "schedule", "schedule_id": int(row["id"])},
                        key,
                        0,
                        iso(now),
                    )
                document = json.loads(row["schedule_json"])
                upcoming = next_run(str(row["kind"]), document, str(row["timezone"]), parse_iso(occurrence))
                while upcoming <= now:
                    upcoming = next_run(str(row["kind"]), document, str(row["timezone"]), upcoming)
                uow.connection.execute(
                    """
                    UPDATE schedules SET last_run_at = ?, next_run_at = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND next_run_at = ?
                    """,
                    (occurrence, iso(upcoming), int(row["id"]), occurrence),
                )
                produced.append(job)
            uow.commit()
        return tuple(produced)


class WebhookSourceService:
    def __init__(self, database_path, clock=None):
        self.database_path = Path(database_path)
        self.clock = clock or utc_now
        run_migrations(self.database_path)

    def create(self, name, downloader, profile_id, enabled=True):
        if not str(name).strip() or not str(downloader).strip():
            raise ValidationError("Webhook name and downloader are required")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with SqliteUnitOfWork(self.database_path) as uow:
            if uow.connection.execute("SELECT 1 FROM scan_profiles WHERE id = ?", (profile_id,)).fetchone() is None:
                raise NotFoundError("Scan profile does not exist", {"id": profile_id})
            cursor = uow.connection.execute(
                """
                INSERT INTO webhook_sources(name, downloader, token_hash, profile_id, enabled)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(name).strip(), str(downloader).strip(), digest, profile_id, int(bool(enabled))),
            )
            row = uow.connection.execute("SELECT * FROM webhook_sources WHERE id = ?", (cursor.lastrowid,)).fetchone()
            uow.commit()
        item = _webhook_from_row(row)
        return CreatedWebhookSource(**item.__dict__, token=token)

    def list(self):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            return tuple(
                _webhook_from_row(row)
                for row in connection.execute(
                    """
                    SELECT w.*
                    FROM webhook_sources w
                    JOIN scan_profiles p ON p.id = w.profile_id
                    WHERE p.deleted_at IS NULL
                    ORDER BY w.id
                    """
                )
            )
        finally:
            connection.close()

    def get(self, source_id):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            row = connection.execute("SELECT * FROM webhook_sources WHERE id = ?", (source_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise NotFoundError("Webhook source does not exist", {"id": source_id})
        return _webhook_from_row(row)

    def update(self, source_id, revision, patch):
        allowed = {"name", "downloader", "profile_id", "enabled"}
        if not patch or set(patch) - allowed:
            raise ValidationError("Unsupported or empty webhook update")
        with SqliteUnitOfWork(self.database_path) as uow:
            row = uow.connection.execute("SELECT * FROM webhook_sources WHERE id = ?", (source_id,)).fetchone()
            if row is None:
                raise NotFoundError("Webhook source does not exist", {"id": source_id})
            if int(row["revision"]) != int(revision):
                raise RevisionConflictError("Webhook source revision is stale")
            name = str(patch.get("name", row["name"])).strip()
            downloader = str(patch.get("downloader", row["downloader"])).strip()
            profile_id = int(patch.get("profile_id", row["profile_id"]))
            enabled = patch.get("enabled", bool(row["enabled"]))
            if not name or not downloader or type(enabled) is not bool:
                raise ValidationError("Webhook update contains invalid values")
            if uow.connection.execute("SELECT 1 FROM scan_profiles WHERE id = ?", (profile_id,)).fetchone() is None:
                raise NotFoundError("Scan profile does not exist", {"id": profile_id})
            uow.connection.execute(
                """
                UPDATE webhook_sources SET name = ?, downloader = ?, profile_id = ?, enabled = ?,
                    revision = revision + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (name, downloader, profile_id, int(enabled), source_id),
            )
            result = uow.connection.execute("SELECT * FROM webhook_sources WHERE id = ?", (source_id,)).fetchone()
            uow.commit()
            return _webhook_from_row(result)

    def delete(self, source_id, revision):
        with SqliteUnitOfWork(self.database_path) as uow:
            row = uow.connection.execute("SELECT revision FROM webhook_sources WHERE id = ?", (source_id,)).fetchone()
            if row is None:
                raise NotFoundError("Webhook source does not exist", {"id": source_id})
            if int(row["revision"]) != int(revision):
                raise RevisionConflictError("Webhook source revision is stale")
            uow.connection.execute("DELETE FROM webhook_sources WHERE id = ?", (source_id,))
            uow.commit()

    def submit_token(self, token, paths):
        digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        targets = [Path(value).expanduser().resolve(strict=False) for value in paths]
        if not targets:
            raise ValidationError("Webhook requires path or paths")
        now = self.clock()
        with SqliteUnitOfWork(self.database_path) as uow:
            row = uow.connection.execute(
                """
                SELECT w.*, p.enabled AS profile_enabled, r.path AS source_path, r.enabled AS root_enabled
                FROM webhook_sources w
                JOIN scan_profiles p ON p.id = w.profile_id
                JOIN storage_roots r ON r.id = p.source_root_id
                WHERE w.token_hash = ? AND w.enabled = 1
                """,
                (digest,),
            ).fetchone()
            if row is None or not bool(row["profile_enabled"]) or not bool(row["root_enabled"]):
                raise NotFoundError("Enabled webhook source does not exist")
            for target in targets:
                if not path_is_within(target, row["source_path"]):
                    raise PathOutsideRootError(
                        "Webhook path is outside the configured source root",
                        {"root": row["source_path"], "target": str(target)},
                    )
            canonical = sorted({str(target) for target in targets}, key=str.casefold)
            facts = []
            for target in canonical:
                try:
                    stat = Path(target).stat()
                    facts.append(f"{normalize_windows_path(target)}:{stat.st_size}:{stat.st_mtime_ns}")
                except OSError:
                    facts.append(f"{normalize_windows_path(target)}:missing")
            key = "webhook:" + hashlib.sha256((f"{row['id']}:" + "|".join(facts)).encode("utf-8")).hexdigest()
            jobs = JobRepository(uow.connection)
            job = jobs.find_by_idempotency_key(key)
            if job is None:
                job = jobs.enqueue(
                    "scan",
                    {"profile_id": int(row["profile_id"]), "paths": canonical, "trigger": "webhook", "webhook_source_id": int(row["id"])},
                    key,
                    0,
                    iso(now),
                )
            uow.connection.execute(
                "UPDATE webhook_sources SET last_called_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (iso(now), int(row["id"])),
            )
            uow.commit()
            return job


class _ProfileEventHandler(FileSystemEventHandler):
    def __init__(self, profile_id, callback):
        self.profile_id = profile_id
        self.callback = callback

    def _record(self, path, is_directory):
        if not is_directory:
            self.callback(self.profile_id, Path(path))

    def on_created(self, event):
        self._record(event.src_path, event.is_directory)

    def on_modified(self, event):
        self._record(event.src_path, event.is_directory)

    def on_moved(self, event):
        self._record(event.dest_path, event.is_directory)


class AutomationRuntime:
    def __init__(
        self,
        database_path,
        queue=None,
        clock=None,
        watch_enabled=True,
        watch_poll_seconds=0.25,
        observer_reload_seconds=2.0,
    ):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)
        self.clock = clock or utc_now
        self.queue = queue or JobQueue(self.database_path, clock=self.clock)
        self.schedules = ScheduleService(self.database_path, clock=self.clock)
        self.watch_enabled = watch_enabled
        self.watch_poll_seconds = max(0.01, float(watch_poll_seconds))
        self.observer_reload_seconds = max(0.01, float(observer_reload_seconds))
        self._watchers = {}
        self._watch_lock = threading.Lock()
        self._last_reload = 0.0
        self.is_running = False

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        if self.watch_enabled:
            self._reload_watchers(force=True)

    def _profiles_to_watch(self):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            return connection.execute(
                """
                SELECT p.id, p.revision, p.stability_seconds, r.path AS source_path
                FROM scan_profiles p JOIN storage_roots r ON r.id = p.source_root_id
                WHERE p.enabled = 1 AND p.watch_enabled = 1 AND r.enabled = 1 ORDER BY p.id
                """
            ).fetchall()
        finally:
            connection.close()

    @staticmethod
    def _stop_observer(state):
        state[1].stop()
        state[1].join(timeout=3)

    def _reload_watchers(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_reload < self.observer_reload_seconds:
            return
        self._last_reload = now
        desired = {
            int(row["id"]): (str(row["source_path"]), int(row["stability_seconds"]), int(row["revision"]))
            for row in self._profiles_to_watch()
        }
        stopped = []
        with self._watch_lock:
            for profile_id, state in list(self._watchers.items()):
                if profile_id not in desired or state[0] != desired[profile_id]:
                    stopped.append(self._watchers.pop(profile_id))
        for state in stopped:
            self._stop_observer(state)
        for profile_id, signature in desired.items():
            with self._watch_lock:
                if profile_id in self._watchers:
                    continue
            source, stability_seconds, unused_revision = signature
            buffer = StableFileBuffer(
                clock=self.clock,
                debounce_seconds=self.watch_poll_seconds,
                stability_seconds=stability_seconds,
            )
            observer = Observer()
            observer.schedule(_ProfileEventHandler(profile_id, self._record_event), source, recursive=True)
            with self._watch_lock:
                self._watchers[profile_id] = (signature, observer, buffer)
            observer.start()

    def _record_event(self, profile_id, path):
        with self._watch_lock:
            state = self._watchers.get(profile_id)
            if state is None:
                return
            try:
                stat = path.stat()
            except OSError:
                return
            state[2].record(path.resolve(strict=False), int(stat.st_size), int(stat.st_mtime_ns))

    def _enqueue_ready_watch_paths(self):
        produced = []
        with self._watch_lock:
            states = list(self._watchers.items())
        for profile_id, state in states:
            buffer = state[2]
            with self._watch_lock:
                if self._watchers.get(profile_id) is not state:
                    continue
                for path in buffer.paths():
                    try:
                        stat = path.stat()
                    except OSError:
                        buffer.discard(path)
                        continue
                    buffer.refresh(path, int(stat.st_size), int(stat.st_mtime_ns))
                ready = buffer.ready()
            if not ready:
                continue
            fingerprints = []
            for path in ready:
                stat = path.stat()
                fingerprints.append(f"{normalize_windows_path(path)}:{stat.st_size}:{stat.st_mtime_ns}")
            key = "watch:" + hashlib.sha256((f"{profile_id}:" + "|".join(fingerprints)).encode("utf-8")).hexdigest()
            produced.append(
                self.queue.enqueue(
                    "scan",
                    {"profile_id": profile_id, "paths": [str(path) for path in ready], "trigger": "watch"},
                    key,
                )
            )
        return tuple(produced)

    def tick(self):
        produced = list(self.schedules.enqueue_due())
        if self.watch_enabled and self.is_running:
            self._reload_watchers()
            produced.extend(self._enqueue_ready_watch_paths())
        return tuple(produced)

    def stop(self):
        with self._watch_lock:
            states = list(self._watchers.values())
            self._watchers.clear()
        for state in states:
            self._stop_observer(state)
        self.is_running = False
