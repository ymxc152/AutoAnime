from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .repository import LibraryRepository, fingerprint
from .models import PlanEntry
from .normalize import same_path
from .path_safety import validate_library_destination


class ExecutionError(RuntimeError):
    pass


class ExecutionFailure(ExecutionError):
    """Execution failure with enough durable context for operator recovery."""

    def __init__(
        self,
        message,
        *,
        log_path,
        applied_records=(),
        rollback_results=(),
        rollback_errors=(),
    ):
        super().__init__(message)
        self.log_path = Path(log_path)
        self.applied_records = tuple(dict(record) for record in applied_records)
        self.rollback_results = tuple(dict(result) for result in rollback_results)
        self.rollback_errors = tuple(str(error) for error in rollback_errors)

    @property
    def partial_rollback(self):
        return bool(self.rollback_errors)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _validate_scanned_source(entry: PlanEntry, path: Optional[Path] = None) -> None:
    if entry.companion_of:
        return
    checked_path = path or entry.source
    stat = checked_path.stat()
    media = entry.resolution.media
    if int(stat.st_size) != int(media.size) or int(stat.st_mtime_ns) != int(media.mtime_ns):
        raise ExecutionError("源文件自扫描后已变化，拒绝移动：%s" % entry.source)


def _stat_signature(path: Path) -> Tuple[int, int, int, int]:
    stat = path.stat()
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def _unique_partial_path(path: Path, label: str) -> Path:
    return path.with_name(".%s.%s.%s.partial" % (path.name, label, uuid.uuid4().hex))


def _restore_staging(staging: Path, source: Path) -> Optional[str]:
    if not staging.exists():
        return None
    if os.name == "nt":
        try:
            os.rename(str(staging), str(source))
        except FileExistsError:
            return "原路径已被新文件占用，待恢复文件保留在：%s" % staging
        except OSError as error:
            return "无法自动恢复源文件，待恢复文件保留在：%s（%s）" % (staging, error)
        return None
    try:
        os.link(str(staging), str(source))
    except FileExistsError:
        return "原路径已被新文件占用，待恢复文件保留在：%s" % staging
    except OSError as error:
        return "无法自动恢复源文件，待恢复文件保留在：%s（%s）" % (staging, error)
    try:
        staging.unlink()
    except Exception as error:
        return "源文件已恢复，但 staging 清理失败并保留在：%s（%s）" % (staging, error)
    return None


def _remove_created_destination(destination: Path, identity: Optional[Tuple[int, int]]) -> Optional[str]:
    if not destination.exists():
        return None
    if identity is None:
        return "无法确认失败目标的身份，已保留：%s" % destination
    stat = destination.stat()
    current_identity = (int(stat.st_dev), int(stat.st_ino))
    if current_identity != identity:
        return "失败目标已被替换，拒绝删除并保留：%s" % destination
    try:
        destination.unlink()
    except Exception as error:
        return "失败目标清理失败并保留在：%s（%s）" % (destination, error)
    return None


def _copy_exclusive(source: Path, destination: Path) -> None:
    created = False
    destination_identity: Optional[Tuple[int, int]] = None
    try:
        with source.open("rb") as source_handle, destination.open("xb") as destination_handle:
            created = True
            destination_stat = os.fstat(destination_handle.fileno())
            destination_identity = (
                int(destination_stat.st_dev),
                int(destination_stat.st_ino),
            )
            shutil.copyfileobj(source_handle, destination_handle, 1024 * 1024)
        shutil.copystat(str(source), str(destination))
    except Exception as error:
        if created:
            cleanup_error = _remove_created_destination(destination, destination_identity)
            if cleanup_error:
                raise ExecutionError("%s；%s" % (error, cleanup_error)) from error
        raise


def _apply_move(entry: PlanEntry) -> str:
    assert entry.destination is not None
    staging = _unique_partial_path(entry.source, "move")
    destination_identity: Optional[Tuple[int, int]] = None
    entry.source.rename(staging)
    try:
        _validate_scanned_source(entry, staging)
        initial_signature = _stat_signature(staging)
        try:
            os.link(str(staging), str(entry.destination))
        except OSError:
            _copy_exclusive(staging, entry.destination)
        destination_stat = entry.destination.stat()
        destination_identity = (int(destination_stat.st_dev), int(destination_stat.st_ino))
        if _stat_signature(staging) != initial_signature:
            raise ExecutionError("移动 staging 在建立目标期间已变化：%s" % staging)
        source_digest = _sha256_file(staging)
        if _stat_signature(staging) != initial_signature:
            raise ExecutionError("移动 staging 在摘要校验期间已变化：%s" % staging)
        destination_size = int(entry.destination.stat().st_size)
        if destination_size != initial_signature[2]:
            raise ExecutionError("移动目标大小校验失败：%s" % entry.destination)
        destination_digest = _sha256_file(entry.destination)
        if _stat_signature(staging) != initial_signature:
            raise ExecutionError("移动 staging 在目标校验期间已变化：%s" % staging)
        if source_digest != destination_digest:
            raise ExecutionError("移动目标摘要校验失败：%s" % entry.destination)
        staging.unlink()
        return destination_digest
    except Exception as error:
        cleanup_error = _remove_created_destination(entry.destination, destination_identity)
        restore_error = _restore_staging(staging, entry.source)
        details = [str(error)]
        if cleanup_error:
            details.append(cleanup_error)
        if restore_error:
            details.append(restore_error)
        raise ExecutionError("；".join(details))


def _apply_one(entry: PlanEntry, mode: str) -> Optional[str]:
    assert entry.destination is not None
    if entry.destination_root is not None:
        validate_library_destination(entry.destination_root, entry.destination)
    entry.destination.parent.mkdir(parents=True, exist_ok=True)
    if entry.destination.exists():
        raise FileExistsError(str(entry.destination))
    if entry.destination_root is not None:
        validate_library_destination(entry.destination_root, entry.destination)
    if mode == "move":
        return _apply_move(entry)
    elif mode == "copy":
        _copy_exclusive(entry.source, entry.destination)
    elif mode == "link":
        os.link(str(entry.source), str(entry.destination))
    else:
        raise ValueError("unsupported mode: " + mode)
    return None


def _rollback_one(entry: PlanEntry, mode: str, expected_sha256: Optional[str] = None) -> None:
    assert entry.destination is not None
    if not entry.destination.exists():
        return
    if expected_sha256 is not None and _sha256_file(entry.destination) != expected_sha256:
        raise ExecutionError("自动回滚目标摘要已变化，拒绝删除或移动：%s" % entry.destination)
    if mode == "move":
        if entry.source.exists():
            raise FileExistsError(str(entry.source))
        entry.source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry.destination), str(entry.source))
    elif mode in {"copy", "link"}:
        entry.destination.unlink()


def execute_plan(
    plan: Iterable[PlanEntry],
    mode: str,
    apply: bool,
    cache: LibraryRepository,
    operation_dir: Optional[Path],
) -> Path:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_dir = operation_dir or (Path.cwd() / ".autoanime-v3" / "operations")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (run_id + ".jsonl")
    completed: List[Tuple[PlanEntry, Optional[str]]] = []
    applied_records = []
    with log_path.open("w", encoding="utf-8") as handle:
        for entry in plan:
            record = entry.to_dict()
            record["resolution"]["fingerprint"] = entry.resolution.fingerprint or fingerprint(entry.resolution.media)
            record["run_id"] = run_id
            record["mode"] = mode
            record["applied"] = False
            if entry.action != "organize" or entry.destination is None:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue
            if not apply:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue
            try:
                applied_sha256 = _apply_one(entry, mode)
                completed.append((entry, None))
                record["applied"] = True
                applied_records.append(dict(record))
                result_stat = entry.destination.stat()
                record["result_size"] = int(result_stat.st_size)
                record["result_mtime_ns"] = int(result_stat.st_mtime_ns)
                result_sha256 = applied_sha256 or _sha256_file(entry.destination)
                record["result_sha256"] = result_sha256
                completed[-1] = (entry, result_sha256)
                applied_records[-1] = dict(record)
                if not entry.companion_of:
                    cache.mark_organized(entry.resolution, entry.destination)
                cache.record_operation(run_id, mode, entry.source, entry.destination, "success")
            except Exception as error:
                record["error"] = str(error)
                cache.record_operation(run_id, mode, entry.source, entry.destination, "failed", str(error))
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                rollback_errors = []
                rollback_results = []
                for previous, expected_sha256 in reversed(completed):
                    try:
                        _rollback_one(previous, mode, expected_sha256)
                        if not previous.companion_of:
                            cache.mark_reverted(previous.resolution)
                        cache.record_operation(run_id, "auto_rollback", previous.destination, previous.source, "success")
                        rollback_record = previous.to_dict()
                        rollback_record.update(
                            {
                                "run_id": run_id,
                                "mode": mode,
                                "applied": False,
                                "auto_rollback": True,
                                "rollback_status": "success",
                            }
                        )
                        handle.write(json.dumps(rollback_record, ensure_ascii=False) + "\n")
                        rollback_results.append(
                            {
                                "source": str(previous.source),
                                "destination": str(previous.destination),
                                "status": "success",
                            }
                        )
                    except Exception as rollback_error:
                        rollback_errors.append(str(rollback_error))
                        rollback_record = previous.to_dict()
                        rollback_record.update(
                            {
                                "run_id": run_id,
                                "mode": mode,
                                "applied": True,
                                "auto_rollback": True,
                                "rollback_status": "failed",
                                "rollback_error": str(rollback_error),
                            }
                        )
                        handle.write(json.dumps(rollback_record, ensure_ascii=False) + "\n")
                        rollback_results.append(
                            {
                                "source": str(previous.source),
                                "destination": str(previous.destination),
                                "status": "failed",
                                "error": str(rollback_error),
                            }
                        )
                message = "整理失败，已自动回滚本批次：%s" % error
                if rollback_errors:
                    message += "；部分回滚失败：" + " | ".join(rollback_errors)
                raise ExecutionFailure(
                    message,
                    log_path=log_path,
                    applied_records=applied_records,
                    rollback_results=rollback_results,
                    rollback_errors=rollback_errors,
                ) from error
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return log_path


def rollback(log_path: Path, cache: Optional[LibraryRepository] = None) -> int:
    records = []
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    restored = 0
    for record in reversed(records):
        if not record.get("applied"):
            continue
        source = Path(record["source"])
        destination = Path(record["destination"])
        mode = record.get("mode")
        if not destination.exists():
            continue
        result_stat = destination.stat()
        expected_size = record.get("result_size")
        expected_mtime_ns = record.get("result_mtime_ns")
        expected_sha256 = record.get("result_sha256")
        if expected_size is not None and int(expected_size) != int(result_stat.st_size):
            raise ExecutionError("回滚目标大小已变化，拒绝删除或移动：%s" % destination)
        if expected_mtime_ns is not None and int(expected_mtime_ns) != int(result_stat.st_mtime_ns):
            raise ExecutionError("回滚目标修改时间已变化，拒绝删除或移动：%s" % destination)
        if mode in {"copy", "link"} and not expected_sha256:
            raise ExecutionError("旧操作日志缺少内容摘要，拒绝删除：%s" % destination)
        if expected_sha256 and _sha256_file(destination) != str(expected_sha256):
            raise ExecutionError("回滚目标内容摘要已变化，拒绝删除或移动：%s" % destination)
        if mode == "link" and source.exists() and not same_path(source, destination):
            raise ExecutionError("回滚目标已不再是原硬链接，拒绝删除：%s" % destination)
        if mode == "move":
            if source.exists():
                raise ExecutionError("回滚目标已存在，拒绝覆盖：%s" % source)
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source))
        elif mode in {"copy", "link"}:
            destination.unlink()
        if cache is not None and not record.get("companion_of"):
            cache.mark_reverted_path(source)
            cache.record_operation(
                str(record.get("run_id") or "manual_rollback"),
                "manual_rollback",
                destination,
                source,
                "success",
            )
        restored += 1
    return restored
