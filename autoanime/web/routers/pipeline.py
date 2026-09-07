from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from autoanime.config import Settings
from autoanime.core.enums import Actor, MemorySource
from autoanime.core.events import Event, EventCategory
from autoanime.core.interfaces import RawName
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.learn import StorageMemoryAccess, learn_confirmation
from autoanime.memory.store import SqliteStorage
from autoanime.organize import confirm_archive
from autoanime.organize.poster import schedule_poster_fetch
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.orchestrator import Orchestrator
from autoanime.scheduler.store import LoopStore
from autoanime.web.deps import (
    BusDep,
    GovernanceDep,
    PosterServiceDep,
    ReferenceChainDep,
    SettingsDep,
    StorageDep,
)
from autoanime.web.learning import (
    ACTION_PENDING_CONFIRM,
    pending_audit_row,
)
from autoanime.web.learning import (
    publish as publish_audit_event,
)
from autoanime.web.schemas import ConfirmNameIn, ConfirmNameOut

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class ParsePreviewIn(BaseModel):
    name: str = Field(min_length=1, max_length=1000)
    folder: str | None = Field(default=None, max_length=1000)
    parent: str | None = Field(default=None, max_length=2000)


class PipelineImportIn(BaseModel):
    directory: str = Field(min_length=1, max_length=2000)
    dry_run: bool = False


def _task_registry(request: Request) -> dict[str, dict[str, object]]:
    tasks: dict[str, dict[str, object]] | None = getattr(request.app.state, "pipeline_tasks", None)
    if tasks is None:
        tasks = {}
        request.app.state.pipeline_tasks = tasks
    return tasks


def _assert_no_running_task(tasks: dict[str, dict[str, object]]) -> None:
    if any(task.get("status") == "running" for task in tasks.values()):
        raise HTTPException(status_code=409, detail="another pipeline task is running")


def _increase(summary: dict[str, int], key: str, amount: int = 1) -> None:
    summary[key] = summary[key] + amount


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _publish(
    bus: Any,
    message: str,
    payload: dict[str, object],
) -> None:
    await bus.publish(Event(EventCategory.SYSTEM, message, payload))


@router.post("/parse-preview")
async def parse_preview(
    body: ParsePreviewIn,
    settings: SettingsDep,
) -> dict[str, object]:
    """单文件 L1 试跑：零网络、零 DB 写入，供 Pipeline 页诊断文件名。"""
    outcome = await Orchestrator(
        LocalRecognizer(),
        l2_enabled=False,
        l3_enabled=False,
    ).process(RawName(name=body.name, folder=body.folder, parent_path=body.parent))
    result = outcome.result
    return {
        "route": outcome.route,
        "result": (
            {
                "title": result.title,
                "season": result.season,
                "episode": result.episode,
                "segment": result.segment.value,
                "fansub": result.fansub,
                "level": result.level.value,
                "confidence": result.confidence,
                "missing_fields": list(result.missing_fields),
                "evidence": dict(result.evidence),
            }
            if result is not None
            else None
        ),
    }


@router.post("/confirm-name", response_model=ConfirmNameOut)
async def confirm_name(
    body: ConfirmNameIn,
    storage: StorageDep,
    governance: GovernanceDep,
    settings: SettingsDep,
    bus: BusDep,
    reference_chain: ReferenceChainDep,
    poster_service: PosterServiceDep,
) -> ConfirmNameOut:
    """库外人工确认学习（12-F）：CLI ``confirm`` 的等价 REST 入口。

    复用 CLI confirm 的同一批入口函数（不复制逻辑）：确认合成
    ``cli.synthesize_confirmation`` → 学习三件套 ``learn_confirmation``
    （parse_memory 两级 + alias 回填）→ 未决 pending 按 raw_name 收尾
    ``LoopStore.resolve_open_pendings_by_raw_name`` → hardlink 归档
    ``cli._archive_confirmed_file``（D17/D21 语义与 CLI 完全一致）。
    文件不在位时归档如实记原因，学习不受影响；bypass 命中不归档。
    """
    # 延迟导入（同 import 任务：避免 web 路由聚合与 CLI 模块初始化成环）。
    from autoanime.cli import (
        _archive_confirmed_file,
        _confirm_entries_payload,
        synthesize_confirmation,
    )

    try:
        confirmed, draft_title = await synthesize_confirmation(
            body.name,
            title=body.title,
            season=body.season,
            episode=body.episode,
            segment=body.segment,
            fansub=body.fansub,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    access = StorageMemoryAccess(storage)
    outcome = await learn_confirmation(
        access,
        confirmed=confirmed,
        raw_name=body.name,
        source=MemorySource.MANUAL,
        bypass_lookup=access,
        reference_lookup=reference_chain,
        draft_title=draft_title,
    )
    # 确认收尾（与 CLI confirm 同语义）：raw_name 匹配的未决 pending 行
    # 一并 resolve，附 pending_confirm 审计行（actor=manual）。
    resolved_rows = await LoopStore(storage).resolve_open_pendings_by_raw_name(
        body.name,
        resolution={"action": "confirm", "confirmed_title": confirmed.title},
        audit_row_for=lambda row: pending_audit_row(
            pending=row, action=ACTION_PENDING_CONFIRM, confirmed=confirmed
        ),
    )
    archive = (
        confirm_archive.ArchiveOutcome(archived=False, reason="bypassed")
        if outcome.bypassed
        else await _archive_confirmed_file(
            confirmed,
            raw_name=body.name,
            resolved_rows=resolved_rows,
            settings=settings,
            governance=governance,
        )
    )
    if archive.archived:
        # 海报兜底（PR3+ 触发点 A）：后台 best-effort，不影响归档结果。
        schedule_poster_fetch(
            poster_service,
            titles=(confirmed.title, None, None),
            library_path=Path(settings.library_path),
        )
    # 端点级确认留痕（12-F）：无 pending 行（纯 parse 场景）也有
    # pending_confirm 惯例的审计行可查；instruction["source"] 区分入口。
    audit = await governance.record_audit(
        operation_id=uuid4().hex,
        entity="pending_queue",
        action=ACTION_PENDING_CONFIRM,
        instruction={
            "raw_name": body.name,
            "source": "confirm-name",
            "resolved_pending": len(resolved_rows),
            "confirmed": {
                "title": confirmed.title,
                "season": confirmed.season,
                "episode": confirmed.episode,
                "segment": confirmed.segment.value,
                "fansub": confirmed.fansub,
            },
        },
        actor=Actor.MANUAL,
    )
    await publish_audit_event(
        bus,
        category=EventCategory.PARSE,
        message="pending.confirmed",
        audit_id=audit.id,
        raw_name=body.name,
        title=confirmed.title,
        bypassed=outcome.bypassed,
    )
    return ConfirmNameOut(
        bypassed=outcome.bypassed,
        resolved_pending=len(resolved_rows),
        archive=archive.as_dict(),
        entries=_confirm_entries_payload(outcome),
    )


@router.post("/import", status_code=202)
async def start_import(
    body: PipelineImportIn,
    request: Request,
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
) -> dict[str, object]:
    """异步导入：立即返回 task_id；进度/结果走任务 API 与 SSE。"""
    root = Path(body.directory)
    if not root.is_absolute():
        raise HTTPException(status_code=422, detail="directory must be an absolute path")
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(status_code=422, detail=f"not a directory: {body.directory}") from None
    if not root.is_dir():
        raise HTTPException(status_code=422, detail=f"not a directory: {body.directory}")
    tasks = _task_registry(request)
    _assert_no_running_task(tasks)
    task_id = uuid4().hex
    info: dict[str, object] = {
        "task_id": task_id,
        "kind": "import",
        "status": "running",
        "directory": str(root),
        "dry_run": body.dry_run,
        "created_at": _now(),
        "finished_at": None,
        "processed": 0,
        "total": None,
        "summary": None,
        "error": None,
    }
    tasks[task_id] = info
    background_tasks.add_task(
        _run_import_task,
        task_id=task_id,
        root=root,
        dry_run=body.dry_run,
        settings=settings,
        tasks=tasks,
        bus=request.app.state.bus,
    )
    return {"task_id": task_id, "status": "running"}


@router.get("/tasks/{task_id}")
async def get_pipeline_task(task_id: str, request: Request) -> dict[str, object]:
    tasks = _task_registry(request)
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return task


async def _run_import_task(
    *,
    task_id: str,
    root: Path,
    dry_run: bool,
    settings: Settings,
    tasks: dict[str, dict[str, object]],
    bus: Any,
) -> None:
    # 延迟导入：web router 聚合可能发生在 CLI 模块初始化过程中。
    from autoanime.cli import _build_orchestrator as build_full_orchestrator
    from autoanime.cli import (
        _group_by_parent,
        _handle_import_outcome,
        _scan_video_files,
    )

    info = tasks[task_id]
    processed = 0
    summary: dict[str, int] = {
        "total": 0,
        "scanned": 0,
        "archived": 0,
        "pending": 0,
        "failed": 0,
        "skipped": 0,
    }
    orchestrator = None
    storage: SqliteStorage | None = None
    transport: Any = None
    owns_storage = False
    try:
        total_seen, videos = _scan_video_files(root)
        info["total"] = len(videos)
        summary["total"] = total_seen
        summary["scanned"] = len(videos)
        if not dry_run:
            try:
                Path(settings.library_path).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(f"library root not preparable: {exc}") from exc

        orchestrator, storage, transport = await build_full_orchestrator(
            settings, metrics=not dry_run, dry_run=dry_run
        )
        owns_storage = storage is None
        if storage is None:
            storage = SqliteStorage(settings.database_url)
            await storage.create_all()
        store = LoopStore(storage)
        governance = MemoryGovernance(storage)
        handled_names: dict[str, str] = {}
        for name in await store.archived_file_names():
            handled_names[name] = "already-archived"
        for name in await store.open_pending_raw_names():
            handled_names.setdefault(name, "already-pending")

        fresh: list[Path] = []
        for file in videos:
            reason = handled_names.get(file.name)
            if reason is None:
                fresh.append(file)
                continue
            _increase(summary, "skipped")
        for parent, files in _group_by_parent(fresh):
            raws = [
                RawName(name=file.name, folder=parent.name, parent_path=str(parent))
                for file in files
            ]
            outcomes = await orchestrator.process_batch(
                raws,
                batching=True,
                batch_min_size=settings.batch_min_size,
                batch_max_size=settings.batch_max_size,
            )
            for file, outcome in zip(files, outcomes, strict=True):
                item = await _handle_import_outcome(
                    file,
                    outcome,
                    settings=settings,
                    store=store,
                    governance=governance,
                    dry_run=dry_run,
                )
                action = str(item["action"])
                if action == "archive":
                    _increase(summary, "archived")
                elif action == "pending":
                    _increase(summary, "pending")
                elif action == "skip":
                    _increase(summary, "skipped")
                else:
                    _increase(summary, "failed")
                processed += 1
                info["processed"] = processed
                await _publish(
                    bus,
                    "pipeline.import.progress",
                    {
                        "task_id": task_id,
                        "processed": processed,
                        "total": info["total"],
                        "file": file.name,
                        "action": action,
                    },
                )
        info["status"] = "completed"
        info["summary"] = summary
        await _publish(
            bus,
            "pipeline.import.completed",
            {"task_id": task_id, **summary},
        )
    except Exception as exc:
        info["status"] = "failed"
        info["error"] = f"{type(exc).__name__}: {exc}"
        await _publish(
            bus,
            "pipeline.import.failed",
            {"task_id": task_id, "error": info["error"]},
        )
    finally:
        try:
            if transport is not None and hasattr(transport, "aclose"):
                await transport.aclose()
        except Exception:
            pass
        if owns_storage and storage is not None:
            await storage.close()
        if info.get("finished_at") is None:
            info["finished_at"] = _now()


__all__ = ["router"]
