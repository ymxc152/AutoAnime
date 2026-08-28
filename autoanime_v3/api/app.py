"""FastAPI app factory and v1 HTTP contract."""

import json
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from autoanime_v3.api.dependencies import CSRF_HEADER, SESSION_COOKIE, csrf_from_request, session_from_request
from autoanime_v3.api.errors import status_for_error
from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import SCHEMA_VERSION, run_migrations
from autoanime_v3.domain.entities import CreateProfile
from autoanime_v3.domain.errors import (
    BootstrapLocalOnlyError,
    DomainError,
    FolderDialogError,
    LocalOnlyError,
)
from autoanime_v3.jobs.queue import JobQueue
from autoanime_v3.security.folder_dialog import pick_folder_windows
from autoanime_v3.security.network import is_loopback_host
from autoanime_v3.security.secrets import DpapiSecretStore, EncryptedFileSecretStore
from autoanime_v3.services.auth import (
    AUTH_LOCAL_BYPASS_KEY,
    LOCAL_HOOK_TRUST_KEY,
    AuthService,
    SecretService,
)
from autoanime_v3.services.settings import (
    METADATA_TMDB_API_KEY_SECRET,
    OPENAI_API_KEY_SECRET,
    OPENAI_BASE_URL_KEY,
    OPENAI_ENABLED_KEY,
    OPENAI_MODEL_KEY,
    OPENAI_TIMEOUT_KEY,
)
from autoanime_v3.services.automation import ScheduleService, WebhookSourceService
from autoanime_v3.services.backups import BackupService
from autoanime_v3.services.changes import ChangeService
from autoanime_v3.services.corrections import CorrectionService
from autoanime_v3.services.jobs import JobService
from autoanime_v3.services.operations import OperationService
from autoanime_v3.services.plans import PlanService
from autoanime_v3.services.profiles import ProfileService
from autoanime_v3.services.reviews import ReviewService
from autoanime_v3.services.roots import RootService
from autoanime_v3.services.rules import RuleService
from autoanime_v3.services.settings import SettingsService


class SPAStaticFiles(StaticFiles):
    """Serve Vite assets while falling back to index.html for client routes."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404 or path.startswith(("api/", "health/")):
                raise
            return await super().get_response("index.html", scope)


@dataclass(frozen=True)
class ServerSettings:
    database_path: Path
    data_directory: Path
    host: str = "0.0.0.0"
    port: int = 8765
    secure_cookies: bool = True
    frontend_directory: Optional[Path] = None
    secret_provider: str = "dpapi"


@dataclass
class ServiceContainer:
    auth: AuthService
    secrets: SecretService
    roots: RootService
    profiles: ProfileService
    queue: JobQueue
    jobs: JobService
    reviews: ReviewService
    plans: PlanService
    operations: OperationService
    rules: RuleService
    changes: ChangeService
    corrections: "CorrectionService"
    settings: SettingsService
    backups: BackupService
    schedules: ScheduleService
    webhooks: WebhookSourceService
    memory: "ShowMemoryService"
    agent_chat: "AgentChatService"
    @classmethod
    def build(cls, settings):
        settings.data_directory.mkdir(parents=True, exist_ok=True)
        secret_store = (
            DpapiSecretStore()
            if settings.secret_provider == "dpapi"
            else EncryptedFileSecretStore(settings.data_directory / "secret-store")
        )
        queue = JobQueue(settings.database_path)
        auth = AuthService(settings.database_path)
        auth.ensure_default_admin()
        app_settings = SettingsService(settings.database_path)
        from autoanime_v3.services.memory import ShowMemoryService
        from autoanime_v3.services.agent_chat import AgentChatService

        return cls(
            auth=auth,
            secrets=SecretService(settings.database_path, secret_store),
            roots=RootService(settings.database_path),
            profiles=ProfileService(settings.database_path),
            queue=queue,
            jobs=JobService(queue),
            reviews=ReviewService(settings.database_path),
            plans=PlanService(settings.database_path),
            operations=OperationService(settings.database_path, settings.data_directory / "operations"),
            rules=RuleService(settings.database_path),
            changes=ChangeService(settings.database_path),
            corrections=CorrectionService(
                settings.database_path, settings.data_directory / "operations"
            ),
            settings=app_settings,
            backups=BackupService(settings.database_path, settings.data_directory / "backups"),
            schedules=ScheduleService(settings.database_path),
            webhooks=WebhookSourceService(settings.database_path),
            memory=ShowMemoryService(settings.database_path),
            agent_chat=AgentChatService(settings.database_path),
        )


class LoginBody(BaseModel):
    username: str
    password: str


class RootBody(BaseModel):
    kind: str
    path: str


class FolderPickBody(BaseModel):
    initial_directory: Optional[str] = None
    title: str = "选择文件夹"


class ProfileBody(BaseModel):
    name: str
    source_root_id: int
    library_root_id: int
    mode: str = "link"
    execution_policy: str = "review_all"
    min_confidence: int = 86
    stability_seconds: int = 30
    watch_enabled: bool = False
    enabled: bool = True


class PatchBody(BaseModel):
    revision: int
    patch: Dict[str, Any]


class RootPatchBody(BaseModel):
    patch: Dict[str, Any]


class SecretBody(BaseModel):
    value: str


class ScanBody(BaseModel):
    profile_id: int
    paths: list = []


class ReviewBody(BaseModel):
    resolution: Any


class RejectBody(BaseModel):
    reason: str


class SettingBody(BaseModel):
    key: str
    value: Any
    revision: int


class RuleSetBody(BaseModel):
    name: str


class RuleRevisionBody(BaseModel):
    rule_set_id: int
    document: Dict[str, Any]


class LibraryChangeBody(BaseModel):
    show_id: int
    base_revision: int
    patch: Dict[str, Any]
    reason: str


class ScheduleBody(BaseModel):
    profile_id: int
    kind: str
    schedule: Dict[str, Any]
    timezone: str = "UTC"
    enabled: bool = True


class WebhookSourceBody(BaseModel):
    name: str
    downloader: str
    profile_id: int
    enabled: bool = True


class DownloaderHookBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: Optional[str] = None
    paths: list[str] = []
    save_path: Optional[str] = None
    savePath: Optional[str] = None
    content_path: Optional[str] = None
    contentPath: Optional[str] = None
    folder: Optional[str] = None


def collect_hook_paths(body) -> list[str]:
    collected = []
    seen = set()
    for value in list(body.paths) + [
        body.path,
        body.save_path,
        body.savePath,
        body.content_path,
        body.contentPath,
        body.folder,
    ]:
        if value and value not in seen:
            seen.add(value)
            collected.append(value)
    return collected


class AgentSessionBody(BaseModel):
    kind: str
    target_id: int


class AgentMessageBody(BaseModel):
    content: str


class DeleteBody(BaseModel):
    revision: int


def serialize(value):
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


def client_host(request: Request):
    return request.client.host if request.client else ""


def set_session_cookie(response: Response, session_token: str, secure_cookies: bool):
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        httponly=True,
        secure=secure_cookies,
        samesite="strict",
        path="/",
        max_age=43200,
    )


def credentials_payload(credentials):
    return {
        "user": serialize(credentials.user),
        "csrf_token": credentials.csrf_token,
        "expires_at": credentials.expires_at,
    }


def create_app(settings, services=None):
    services = services or ServiceContainer.build(settings)
    # Ensure default security settings exist as soon as the app starts.
    services.settings.ensure_defaults()
    # Backfill library entries for executions that completed before shows-sync
    # existed, so already-organized anime shows up on the 资料库 page.
    try:
        services.corrections.backfill_library()
    except Exception:
        # Backfill is best-effort; a failure must never block startup.
        pass
    app = FastAPI(title="AutoAnime Web Console", version="3.0")
    app.state.settings = settings
    app.state.services = services

    @app.middleware("http")
    async def trace_middleware(request, call_next):
        request.state.trace_id = request.headers.get("X-Trace-ID") or secrets.token_hex(16)
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(request, error):
        return JSONResponse(
            status_code=status_for_error(error),
            content={
                "code": error.code,
                "message": error.message,
                "details": error.details,
                "trace_id": request.state.trace_id,
            },
        )

    def current_user(request: Request):
        return services.auth.authenticate(session_from_request(request))

    def changing_user(request: Request):
        return services.auth.require_csrf(
            session_from_request(request), csrf_from_request(request)
        )

    def rows(sql, params=()):
        connection = connect_sqlite(settings.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]
        finally:
            connection.close()

    @app.get("/health/live")
    def live():
        return {"status": "live"}

    @app.get("/health/ready")
    def ready():
        run_migrations(settings.database_path)
        return {"status": "ready", "schema_version": SCHEMA_VERSION}

    @app.get("/api/v1/auth/bootstrap-status")
    def bootstrap_status(request: Request):
        host = client_host(request)
        loopback = is_loopback_host(host)
        configured = bool(rows("SELECT 1 FROM users LIMIT 1"))
        local_bypass = services.auth.local_bypass_enabled()
        return {
            "configured": configured,
            "local_bypass": local_bypass,
            "local_client": loopback,
            "can_local_login": bool(loopback and local_bypass and configured),
        }

    @app.post("/api/v1/auth/bootstrap", status_code=201)
    def bootstrap(body: LoginBody, request: Request):
        host = client_host(request)
        if not is_loopback_host(host):
            raise BootstrapLocalOnlyError(
                "The first administrator must be created from the local machine"
            )
        return serialize(services.auth.bootstrap_admin(body.username, body.password))

    @app.post("/api/v1/auth/login")
    def login(body: LoginBody, request: Request, response: Response):
        credentials = services.auth.login(
            body.username,
            body.password,
            client_host(request),
            request.headers.get("User-Agent"),
        )
        set_session_cookie(response, credentials.session_token, settings.secure_cookies)
        return credentials_payload(credentials)

    @app.post("/api/v1/auth/local-session")
    def local_session(request: Request, response: Response):
        host = client_host(request)
        credentials = services.auth.local_session(
            client_ip=host,
            user_agent=request.headers.get("User-Agent"),
            is_loopback=is_loopback_host(host),
        )
        set_session_cookie(response, credentials.session_token, settings.secure_cookies)
        return credentials_payload(credentials)

    @app.get("/api/v1/auth/me")
    def me(user=Depends(current_user)):
        return serialize(user)

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response, user=Depends(changing_user)):
        services.auth.logout(session_from_request(request))
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.status_code = 204
        return response

    @app.get("/api/v1/dashboard")
    def dashboard(user=Depends(current_user)):
        counts = rows(
            """
            SELECT
              (SELECT COUNT(*) FROM jobs WHERE status IN ('queued','leased','running')) AS active_jobs,
              (SELECT COUNT(*) FROM review_items WHERE status = 'open') AS open_reviews,
              (SELECT COUNT(*) FROM plan_items WHERE execution_status = 'conflict') AS conflicts,
              (SELECT COUNT(*) FROM jobs WHERE status = 'failed') AS failed_jobs
            """
        )[0]
        counts["learned_aliases"] = rows("SELECT COUNT(*) AS count FROM learned_show_memory")[0]["count"]
        counts["webhook_count"] = rows("SELECT COUNT(*) AS count FROM webhook_sources")[0]["count"]
        counts["schedule_count"] = rows("SELECT COUNT(*) AS count FROM schedules")[0]["count"]
        counts["recent_titles"] = [
            row["canonical_title"]
            for row in rows(
                """
                SELECT DISTINCT title AS canonical_title
                FROM identification_results
                WHERE title != '' AND title IS NOT NULL
                ORDER BY id DESC LIMIT 8
                """
            )
        ]
        counts["roots"] = rows("SELECT id, kind, path, health_status, enabled FROM storage_roots ORDER BY id")
        counts["recent_jobs"] = rows(
            "SELECT id, job_type, status, current_stage, progress_current, progress_total, created_at FROM jobs ORDER BY id DESC LIMIT 8"
        )
        return counts

    @app.get("/api/v1/memory")
    def list_memory(user=Depends(current_user)):
        return {"items": services.memory.list(), "next_cursor": None}

    @app.post("/api/v1/agent/sessions", status_code=201)
    def open_agent_session(body: AgentSessionBody, user=Depends(current_user)):
        return services.agent_chat.open_session(body.kind, body.target_id)

    @app.get("/api/v1/agent/sessions/{session_id}")
    def get_agent_session(session_id: int, user=Depends(current_user)):
        return services.agent_chat.get(session_id)

    @app.post("/api/v1/agent/sessions/{session_id}/messages")
    def add_agent_message(session_id: int, body: AgentMessageBody, user=Depends(changing_user)):
        return services.agent_chat.add_message(session_id, body.content)

    @app.post("/api/v1/agent/sessions/{session_id}/apply")
    def apply_agent_session(session_id: int, user=Depends(changing_user)):
        return services.agent_chat.apply(session_id, user.id)

    @app.post("/api/v1/agent/sessions/{session_id}/abandon")
    def abandon_agent_session(session_id: int, user=Depends(changing_user)):
        return services.agent_chat.abandon(session_id)

    @app.get("/api/v1/roots")
    def list_roots(user=Depends(current_user)):
        return {
            "items": rows(
                """
                SELECT r.*,
                       (SELECT COUNT(*)
                        FROM scan_profiles p
                        WHERE p.source_root_id = r.id OR p.library_root_id = r.id) AS profile_count,
                       (SELECT COUNT(*) FROM file_locations fl WHERE fl.root_id = r.id) AS file_count
                FROM storage_roots r
                ORDER BY r.id
                """
            ),
            "next_cursor": None,
        }

    @app.post("/api/v1/roots", status_code=201)
    def create_root(body: RootBody, user=Depends(changing_user)):
        return serialize(services.roots.create_root(body.kind, Path(body.path)))

    @app.post("/api/v1/system/pick-folder")
    def pick_folder(body: FolderPickBody, request: Request, user=Depends(changing_user)):
        """Open a native Windows folder dialog on the server host.

        Restricted to loopback clients so a LAN browser cannot pop dialogs on
        the machine running AutoAnimeWeb.
        """
        host = client_host(request)
        if not is_loopback_host(host):
            raise LocalOnlyError("Folder picker is only available on the local machine")
        try:
            selected = pick_folder_windows(body.initial_directory, body.title)
        except Exception as error:  # pragma: no cover - OS dialog surface
            from autoanime_v3.security.folder_dialog import FolderDialogError as NativeFolderDialogError

            if isinstance(error, NativeFolderDialogError):
                raise FolderDialogError(error.message, {"code": error.code}) from error
            raise FolderDialogError(str(error) or "Folder dialog failed") from error
        return {"path": selected, "cancelled": selected is None}

    @app.patch("/api/v1/roots/{root_id}")
    def patch_root(root_id: int, body: RootPatchBody, user=Depends(changing_user)):
        return serialize(services.roots.update_root(root_id, body.patch))

    @app.delete("/api/v1/roots/{root_id}", status_code=204)
    def delete_root(root_id: int, response: Response, user=Depends(changing_user)):
        services.roots.delete_root(root_id)
        response.status_code = 204
        return response

    @app.post("/api/v1/roots/{root_id}/validate")
    def validate_root(root_id: int, user=Depends(changing_user)):
        return serialize(services.roots.validate_root(root_id))

    @app.get("/api/v1/profiles")
    def list_profiles(user=Depends(current_user)):
        return {
            "items": rows(
                """
                SELECT p.*,
                       (SELECT COUNT(*) FROM scan_runs sr WHERE sr.profile_id = p.id) AS scan_runs,
                       (SELECT COUNT(*) FROM plans pl WHERE pl.profile_id = p.id) AS plans
                FROM scan_profiles p
                WHERE p.deleted_at IS NULL
                ORDER BY p.id
                """
            ),
            "next_cursor": None,
        }

    @app.post("/api/v1/profiles", status_code=201)
    def create_profile(body: ProfileBody, user=Depends(changing_user)):
        return serialize(services.profiles.create_profile(CreateProfile(**body.model_dump())))

    @app.patch("/api/v1/profiles/{profile_id}")
    def patch_profile(profile_id: int, body: PatchBody, user=Depends(changing_user)):
        return serialize(services.profiles.update_profile(profile_id, body.revision, body.patch))

    @app.delete("/api/v1/profiles/{profile_id}", status_code=204)
    def delete_profile(profile_id: int, body: DeleteBody, response: Response, user=Depends(changing_user)):
        services.profiles.delete_profile(profile_id, body.revision)
        response.status_code = 204
        return response

    @app.get("/api/v1/schedules")
    def list_schedules(user=Depends(current_user)):
        return {"items": [serialize(item) for item in services.schedules.list()], "next_cursor": None}

    @app.post("/api/v1/schedules", status_code=201)
    def create_schedule(body: ScheduleBody, user=Depends(changing_user)):
        return serialize(
            services.schedules.create(
                body.profile_id, body.kind, body.schedule, body.timezone, body.enabled
            )
        )

    @app.patch("/api/v1/schedules/{schedule_id}")
    def patch_schedule(schedule_id: int, body: PatchBody, user=Depends(changing_user)):
        return serialize(services.schedules.update(schedule_id, body.revision, body.patch))

    @app.delete("/api/v1/schedules/{schedule_id}", status_code=204)
    def delete_schedule(schedule_id: int, body: DeleteBody, response: Response, user=Depends(changing_user)):
        services.schedules.delete(schedule_id, body.revision)
        response.status_code = 204
        return response

    @app.get("/api/v1/webhook-sources")
    def list_webhook_sources(user=Depends(current_user)):
        return {"items": [serialize(item) for item in services.webhooks.list()], "next_cursor": None}

    @app.post("/api/v1/webhook-sources", status_code=201)
    def create_webhook_source(body: WebhookSourceBody, user=Depends(changing_user)):
        return serialize(
            services.webhooks.create(
                body.name, body.downloader, body.profile_id, body.enabled
            )
        )

    @app.patch("/api/v1/webhook-sources/{source_id}")
    def patch_webhook_source(source_id: int, body: PatchBody, user=Depends(changing_user)):
        return serialize(services.webhooks.update(source_id, body.revision, body.patch))

    @app.delete("/api/v1/webhook-sources/{source_id}", status_code=204)
    def delete_webhook_source(source_id: int, body: DeleteBody, response: Response, user=Depends(changing_user)):
        services.webhooks.delete(source_id, body.revision)
        response.status_code = 204
        return response

    @app.post("/api/v1/hooks/downloaders/{token}", status_code=202)
    def downloader_hook(token: str, body: DownloaderHookBody):
        paths = collect_hook_paths(body)
        return serialize(services.webhooks.submit_token(token, paths))

    @app.post("/api/v1/hooks/local", status_code=202)
    def local_downloader_hook(body: DownloaderHookBody, request: Request):
        host = client_host(request)
        if not is_loopback_host(host):
            raise LocalOnlyError("Local hook endpoint is only available on loopback")
        if not services.auth.local_hook_trust_enabled():
            raise LocalOnlyError("Local trusted hooks are disabled")
        paths = collect_hook_paths(body)
        profile_id = None
        if paths:
            # Prefer the first enabled profile whose source root contains the path.
            for profile in rows(
                """
                SELECT p.id AS profile_id, r.path AS source_path
                FROM scan_profiles p
                JOIN storage_roots r ON r.id = p.source_root_id
                WHERE p.enabled = 1 AND r.enabled = 1
                ORDER BY p.id
                """
            ):
                from autoanime_v3.services.roots import path_is_within

                if any(path_is_within(Path(path).expanduser(), profile["source_path"]) for path in paths):
                    profile_id = int(profile["profile_id"])
                    break
        if profile_id is None:
            enabled = rows("SELECT id FROM scan_profiles WHERE enabled = 1 ORDER BY id LIMIT 1")
            if not enabled:
                from autoanime_v3.domain.errors import NotFoundError

                raise NotFoundError("No enabled scan profile is available for local hook")
            profile_id = int(enabled[0]["id"])
        return serialize(
            services.jobs.submit_scan(profile_id, paths, f"local-hook:{profile_id}:{secrets.token_hex(8)}")
        )

    @app.post("/api/v1/jobs/scans", status_code=202)
    def submit_scan(
        body: ScanBody,
        user=Depends(changing_user),
        idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    ):
        return serialize(services.jobs.submit_scan(body.profile_id, body.paths, idempotency_key))

    @app.get("/api/v1/jobs")
    def list_jobs(user=Depends(current_user)):
        return {"items": rows("SELECT * FROM jobs ORDER BY id DESC LIMIT 100"), "next_cursor": None}

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: int, user=Depends(current_user)):
        return serialize(services.jobs.get(job_id))

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: int, user=Depends(changing_user)):
        return serialize(services.jobs.cancel(job_id))

    @app.get("/api/v1/jobs/{job_id}/events")
    def job_events(job_id: int, request: Request, user=Depends(current_user)):
        last = int(request.headers.get("Last-Event-ID", "0") or 0)
        events = services.jobs.events(job_id, last)

        def stream():
            for event in events:
                yield "id: %s\nevent: %s\ndata: %s\n\n" % (
                    event.sequence,
                    event.event_type,
                    json.dumps(serialize(event), ensure_ascii=False),
                )

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/v1/reviews")
    def list_reviews(user=Depends(current_user)):
        return {"items": [serialize(item) for item in services.reviews.list_open()], "next_cursor": None}

    @app.post("/api/v1/reviews/{review_id}/resolve")
    def resolve_review(review_id: int, body: ReviewBody, user=Depends(changing_user)):
        return serialize(services.reviews.resolve(review_id, body.resolution, user.id))

    @app.get("/api/v1/plans")
    def list_plans(user=Depends(current_user)):
        items = rows("SELECT * FROM plans ORDER BY id DESC LIMIT 100")
        for item in items:
            snapshot = json.loads(item.get("profile_snapshot_json") or "{}")
            item["profile_snapshot"] = snapshot
            item["profile_name"] = snapshot.get("name")
        return {"items": items, "next_cursor": None}

    @app.get("/api/v1/plans/{plan_id}")
    def get_plan(plan_id: int, user=Depends(current_user)):
        return serialize(services.plans.get(plan_id))

    @app.delete("/api/v1/plans/{plan_id}", status_code=204)
    def delete_plan(plan_id: int, response: Response, user=Depends(changing_user)):
        services.plans.delete_plan(plan_id)
        response.status_code = 204
        return response

    @app.post("/api/v1/plans/{plan_id}/items/{item_id}/approve")
    def approve_plan_item(plan_id: int, item_id: int, user=Depends(changing_user)):
        return serialize(services.plans.decide_item(plan_id, item_id, "approved", user.id))

    @app.post("/api/v1/plans/{plan_id}/items/{item_id}/reject")
    def reject_plan_item(
        plan_id: int,
        item_id: int,
        body: RejectBody,
        user=Depends(changing_user),
    ):
        return serialize(
            services.plans.decide_item(plan_id, item_id, "rejected", user.id, body.reason)
        )

    @app.post("/api/v1/plans/{plan_id}/approve")
    def approve_plan(plan_id: int, user=Depends(changing_user)):
        plan, job = services.plans.approve_and_enqueue(plan_id, user.id)
        return {"plan": serialize(plan), "job": serialize(job)}

    @app.post("/api/v1/plans/{plan_id}/execute-approved")
    def execute_approved_plan(plan_id: int, user=Depends(changing_user)):
        plan, job = services.plans.enqueue_approved_execution(plan_id, user.id)
        return {"plan": serialize(plan), "job": serialize(job)}

    @app.get("/api/v1/operations")
    def list_operations(user=Depends(current_user)):
        items = rows(
            """
            SELECT ob.*, p.profile_snapshot_json
            FROM operation_batches ob
            LEFT JOIN plans p ON p.id = ob.plan_id
            ORDER BY ob.id DESC LIMIT 100
            """
        )
        for item in items:
            snapshot = json.loads(item.pop("profile_snapshot_json", None) or "{}")
            item["profile_snapshot"] = snapshot
            item["profile_name"] = snapshot.get("name")
        return {"items": items, "next_cursor": None}

    @app.get("/api/v1/operations/{batch_id}")
    def get_operation(batch_id: int, user=Depends(current_user)):
        return serialize(services.operations.get(batch_id))

    @app.post("/api/v1/operations/{batch_id}/rollback", status_code=202)
    def rollback_operation(batch_id: int, user=Depends(changing_user)):
        services.operations.validate_rollback(batch_id)
        return serialize(
            services.queue.enqueue(
                "rollback_operation",
                {"batch_id": batch_id, "requested_by": user.id},
                "rollback-operation:%s" % batch_id,
            )
        )

    @app.get("/api/v1/library/shows")
    def library_shows(user=Depends(current_user), q: Optional[str] = None, sort: str = "title"):
        where = ""
        params = []
        if q and q.strip():
            escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where = "WHERE s.canonical_title LIKE ? ESCAPE '\\'"
            params = ["%%%s%%" % escaped]
        order = (
            "ORDER BY s.canonical_title"
            if sort != "recent"
            else "ORDER BY recent_activity DESC"
        )
        sql = f"""
            SELECT s.id, s.canonical_title, s.normalized_key, s.status, s.title_locked,
                   s.revision, s.created_at, s.updated_at,
                   COALESCE((
                     SELECT MAX(activity.ts) FROM (
                       SELECT updated_at AS ts FROM seasons WHERE show_id = s.id
                       UNION ALL
                       SELECT e.updated_at FROM episodes e
                         JOIN seasons se ON se.id = e.season_id WHERE se.show_id = s.id
                       UNION ALL
                       SELECT ma.updated_at FROM media_assignments ma WHERE ma.show_id = s.id
                     ) activity
                   ), s.updated_at) AS recent_activity,
                   (SELECT COUNT(*) FROM seasons WHERE show_id = s.id) AS season_count,
                   (SELECT COUNT(*) FROM episodes e
                      JOIN seasons se ON se.id = e.season_id WHERE se.show_id = s.id) AS episode_count
            FROM shows s
            {where}
            {order}
        """
        return {"items": rows(sql, params), "next_cursor": None}

    @app.get("/api/v1/library/shows/{show_id}")
    def library_show(show_id: int, user=Depends(current_user)):
        show = rows("SELECT * FROM shows WHERE id = ?", (show_id,))
        seasons = rows("SELECT * FROM seasons WHERE show_id = ? ORDER BY season_number", (show_id,))
        metadata = rows("SELECT * FROM metadata_records WHERE show_id = ? ORDER BY fetched_at DESC", (show_id,))
        for season in seasons:
            episode_rows = rows(
                """
                SELECT e.id, e.season_id, e.episode_number, e.episode_type, e.display_title,
                       e.sort_value, e.created_at, e.updated_at,
                       ma.release_label, fl.path AS file_path, fl.state AS file_state
                FROM episodes e
                LEFT JOIN media_assignments ma ON ma.episode_id = e.id
                LEFT JOIN file_locations fl ON fl.media_file_id = ma.media_file_id
                     AND fl.role = 'library'
                WHERE e.season_id = ?
                ORDER BY e.sort_value
                """,
                (season["id"],),
            )
            by_id = {}
            for row in episode_rows:
                entry = by_id.get(row["id"])
                if entry is None:
                    entry = {
                        key: value
                        for key, value in row.items()
                        if key not in ("file_path", "file_state", "release_label")
                    }
                    entry["files"] = []
                    by_id[row["id"]] = entry
                if row["file_path"]:
                    entry["files"].append(
                        {
                            "path": row["file_path"],
                            "state": row["file_state"],
                            "release_label": row["release_label"],
                        }
                    )
            season["episodes"] = list(by_id.values())
        return {"show": show[0] if show else None, "seasons": seasons, "metadata": metadata}

    @app.get("/api/v1/library/files/{media_id}")
    def library_file(media_id: int, user=Depends(current_user)):
        media = rows("SELECT * FROM media_files WHERE id = ?", (media_id,))
        locations = rows("SELECT * FROM file_locations WHERE media_file_id = ?", (media_id,))
        return {"media": media[0] if media else None, "locations": locations}

    @app.post("/api/v1/library/changes/preview", status_code=201)
    def preview_library_change(body: LibraryChangeBody, user=Depends(changing_user)):
        return serialize(
            services.changes.preview_show_change(
                body.show_id, body.base_revision, body.patch, body.reason
            )
        )

    @app.post("/api/v1/library/changes/impact")
    def library_change_impact(body: LibraryChangeBody, user=Depends(current_user)):
        return serialize(
            services.corrections.impact(
                body.show_id, str(body.patch.get("canonical_title") or "")
            )
        )

    @app.post("/api/v1/library/changes/{request_id}/approve")
    def approve_library_change(request_id: int, user=Depends(changing_user)):
        return serialize(services.corrections.apply(request_id, user.id))

    @app.get("/api/v1/rules")
    def rules(user=Depends(current_user)):
        items = rows("SELECT * FROM rule_sets ORDER BY id")
        for item in items:
            revisions = rows(
                "SELECT * FROM rule_revisions WHERE rule_set_id = ? ORDER BY revision DESC",
                (item["id"],),
            )
            for revision in revisions:
                revision["document"] = json.loads(revision.pop("document_json"))
                revision["validation_errors"] = json.loads(
                    revision.pop("validation_errors_json") or "[]"
                )
            item["revisions"] = revisions
        return {"items": items, "next_cursor": None}

    @app.post("/api/v1/rules", status_code=201)
    def create_rule_set(body: RuleSetBody, user=Depends(changing_user)):
        return serialize(services.rules.create_set(body.name))

    @app.post("/api/v1/rules/revisions", status_code=201)
    def create_rule_revision(body: RuleRevisionBody, user=Depends(changing_user)):
        return serialize(services.rules.create_revision(body.rule_set_id, body.document))

    @app.post("/api/v1/rules/revisions/{revision_id}/validate")
    def validate_rule_revision(revision_id: int, user=Depends(changing_user)):
        return serialize(services.rules.validate(revision_id))

    @app.post("/api/v1/rules/revisions/{revision_id}/activate")
    def activate_rule_revision(revision_id: int, user=Depends(changing_user)):
        return serialize(services.rules.activate(revision_id))

    @app.post("/api/v1/rules/{rule_set_id}/revisions/{revision_id}/rollback")
    def rollback_rule_revision(rule_set_id: int, revision_id: int, user=Depends(changing_user)):
        return serialize(services.rules.rollback(rule_set_id, revision_id))

    @app.get("/api/v1/settings")
    def settings_view(user=Depends(current_user)):
        items = services.settings.list()
        by_key = {item["key"]: item for item in items}
        secret_rows = rows(
            "SELECT key, provider, updated_at, 1 AS configured FROM secret_settings ORDER BY key"
        )
        openai_key_configured = any(row["key"] == OPENAI_API_KEY_SECRET for row in secret_rows)
        metadata_key_configured = any(row["key"] == METADATA_TMDB_API_KEY_SECRET for row in secret_rows)
        return {
            "items": items,
            "security": {
                "local_bypass": bool(
                    by_key.get(AUTH_LOCAL_BYPASS_KEY, {}).get("value", True)
                ),
                "local_bypass_revision": int(
                    by_key.get(AUTH_LOCAL_BYPASS_KEY, {}).get("revision", 0)
                ),
                "local_hook_trust": bool(
                    by_key.get(LOCAL_HOOK_TRUST_KEY, {}).get("value", True)
                ),
                "local_hook_trust_revision": int(
                    by_key.get(LOCAL_HOOK_TRUST_KEY, {}).get("revision", 0)
                ),
            },
            "openai": services.settings.openai_public_view(openai_key_configured),
            "metadata": services.settings.metadata_public_view(metadata_key_configured),
            "secrets": secret_rows,
        }

    @app.patch("/api/v1/settings")
    def update_setting(body: SettingBody, user=Depends(changing_user)):
        return services.settings.update(body.key, body.value, body.revision)

    @app.put("/api/v1/settings/secrets/{key}")
    def update_secret(key: str, body: SecretBody, user=Depends(changing_user)):
        allowed = {METADATA_TMDB_API_KEY_SECRET, "metadata.api_key", OPENAI_API_KEY_SECRET}
        if key not in allowed:
            from autoanime_v3.domain.errors import ValidationError

            raise ValidationError(
                "Unsupported secret key",
                {"key": key, "allowed": sorted(allowed)},
            )
        return serialize(services.secrets.set_secret(key, body.value))

    @app.post("/api/v1/backups", status_code=201)
    def create_backup(user=Depends(changing_user)):
        return serialize(services.backups.create())

    @app.get("/api/v1/backups")
    def list_backups(user=Depends(current_user)):
        return {"items": rows("SELECT * FROM backup_records ORDER BY id DESC"), "next_cursor": None}

    if settings.frontend_directory and Path(settings.frontend_directory).is_dir():
        app.mount("/", SPAStaticFiles(directory=str(settings.frontend_directory), html=True), name="webui")

    return app
