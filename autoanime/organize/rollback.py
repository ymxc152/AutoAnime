"""audit reverse instruction 执行器（E4b，ARCHITECTURE 5.4）。

与 E2 的 rollback 端点共用语义：``audit_log.reverse`` 是结构化 JSON——
- ``{"status": <MemoryStatus>}``（parse_memory 状态恢复）→ E2 端点既有
  引擎负责（web/routers/organize.py）；
- ``{"moves": [{src, dst, kind, role}...]}``（organize 文件反操作）→
  本模块 :func:`execute_reverse` 负责：hardlink/copy 落地的归档文件删除，
  下载原件（做种侧）永不触碰（D21）；
- ``{"replaced": {dst, origin}}``（洗版替换的旧文件恢复）→ 尽力恢复。

:func:`split_reverse` 与 E2 的 ``_split_reverse`` 同构：按键拆
「本域可执行」与「其余如实 skipped」，不静默丢弃。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: reverse 指令里 organize 域可执行的键。
ORGANIZE_REVERSE_KEYS = frozenset({"moves", "replaced"})


def split_reverse(reverse: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    """拆分 reverse 指令：返回 (organize 可执行部分, 其余部分)。"""
    executable: dict[str, object] = {}
    skipped: dict[str, object] = {}
    for key, value in reverse.items():
        if key in ORGANIZE_REVERSE_KEYS:
            executable[key] = value
        else:
            skipped[key] = value
    return executable, skipped


def _undo_move(entry: dict[str, Any]) -> str | None:
    """单条搬移反操作：删归档侧链接/拷贝；返回 skipped 原因或 None。"""
    raw_dst = str(entry.get("dst") or "")
    kind = str(entry.get("kind") or "hardlink")
    if not raw_dst:
        return "missing dst"
    dst = Path(raw_dst)
    if not dst.exists():
        return "already gone"
    if kind == "hardlink":
        try:
            src = Path(str(entry.get("src") or ""))
            if src.exists() and src.samefile(dst):
                dst.unlink()
                return None
            # 链接被替换过（同名新文件）：不误删，如实跳过。
            return "dst no longer links to src"
        except OSError as exc:
            return f"unlink failed: {type(exc).__name__}"
    try:
        dst.unlink()
        return None
    except OSError as exc:
        return f"unlink failed: {type(exc).__name__}"


def _restore_replaced(entry: dict[str, Any]) -> str | None:
    """洗版替换的旧文件恢复：从 origin（旧下载原件）重建归档侧文件。"""
    dst = Path(str(entry.get("dst") or ""))
    origin = Path(str(entry.get("origin") or ""))
    if not dst or not origin:
        return "missing dst/origin"
    if dst.exists():
        return None  # 已有文件（用户手工恢复或后续归档）：不动
    if not origin.exists():
        return "origin gone"
    try:
        import os

        os.link(origin, dst)
        return None
    except OSError:
        import shutil

        try:
            shutil.copy2(origin, dst)
        except OSError as exc:
            return f"restore failed: {type(exc).__name__}"
    return None


def execute_reverse(reverse: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """执行 organize 域反操作；返回 (applied, skipped) 明细（与 E2 端点同形）。"""
    applied: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    moves = reverse.get("moves")
    if isinstance(moves, list):
        for entry in moves:
            if not isinstance(entry, dict):
                skipped.append({"entry": entry, "reason": "malformed"})
                continue
            reason = _undo_move(entry)
            if reason is None:
                applied.append({"move": entry})
            else:
                skipped.append({"move": entry, "reason": reason})
    replaced = reverse.get("replaced")
    if isinstance(replaced, dict):
        reason = _restore_replaced(replaced)
        if reason is None:
            applied.append({"replaced": replaced})
        else:
            skipped.append({"replaced": replaced, "reason": reason})
    return applied, skipped
