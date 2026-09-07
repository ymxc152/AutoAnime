from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from autoanime.config import Settings
from autoanime.core.events import Event, EventCategory
from autoanime.core.interfaces import RawName
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.orchestrator import Orchestrator
from autoanime.scheduler.store import LoopStore
from autoanime.web.deps import SettingsDep

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


@router.post("/import", status_code=202)
async def start_import(
    body: PipelineImportIn,
    request: Request,
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
) -> dict[str, object]:
    """异步导入：立即返回 task_id；进度/结果走任务 API 与 SSE。"""
    root = Path(body.directory)
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
            settings, metrics=not dry_run
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
