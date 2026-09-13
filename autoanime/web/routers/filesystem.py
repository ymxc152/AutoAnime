"""目录浏览端点（P1-D）：GET /api/filesystem?path= ... 供前端导入路径选择器逐级下钻。

纯只读浏览：仅列子目录名（不读文件内容、不写任何状态），无副作用，因此
**不写 audit**——audit 面向「有意义的状态变更」（对齐 settings/organize 等
路由的口径），纯浏览只会产生噪音行。

- path 缺省/空：Windows 枚举存在的盘符（``A:\\``..``Z:\\``，按字母序）；
  非 Windows 平台无盘符概念，兜底返回根目录（跨平台兜底，见下）。
- path 非空：``Path(path).resolve()`` 后用 ``os.scandir`` 仅列**子目录**
  （``is_dir(follow_symlinks=False)`` 防符号链接环），按名 casefold 排序，
  截断 ``_MAX_ENTRIES``（500）；单条条目的 PermissionError/OSError 跳过，
  scandir 整体失败（如目录无读权限）返回空列表而非 500。
- path 不存在/非目录 → 404 ``dir_not_found``。
- 认证：app.py 的 token 中间件覆盖全部 /api 路由（含本端点），无需单独处理。
"""

from __future__ import annotations

import os
import string
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/filesystem", tags=["filesystem"])

#: 目录列表截断上限（超大目录防止响应膨胀；前端逐级下钻不受影响）。
_MAX_ENTRIES = 500


class FilesystemListing(BaseModel):
    """GET /api/filesystem 响应（前端 FilesystemListing 同构）。

    files 仅在 ``include_files=true`` 时填充（选番抽屉的文件选择模式）；
    默认空列表保持既有响应形状不变。
    """

    path: str
    parent: str | None
    directories: list[str]
    files: list[str] = []


def _list_drives() -> list[str]:
    """Windows 盘符探测：存在即列入（``C:\\`` 形态，带尾反斜杠）。"""
    return [
        f"{letter}:\\"
        for letter in string.ascii_uppercase
        if os.path.exists(f"{letter}:\\")
    ]


def _list_subdirectories(resolved: Path) -> list[str]:
    """仅列子目录名：单条 PermissionError/OSError 跳过；scandir 整体失败 → 空表。"""
    directories: list[str] = []
    try:
        with os.scandir(resolved) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(entry.name)
                except OSError:
                    continue  # 单条不可访问（权限/竞态删除）跳过，不中断
    except OSError:
        # scandir 本身失败（如目录无读权限）：返回空列表，让用户仍可
        # 「选择当前目录」或经面包屑返回上级，而不是整弹窗报错。
        return []
    return sorted(directories, key=str.casefold)[:_MAX_ENTRIES]


@router.get("", response_model=FilesystemListing)
async def browse(
    path: str = Query(default=""),
    include_files: bool = Query(default=False),
) -> FilesystemListing:
    if path.strip() == "":
        if os.name == "nt":
            return FilesystemListing(path="", parent=None, directories=_list_drives())
        # 非 Windows 平台无盘符概念：兜底返回根目录（列表项与 path 同为 /，
        # parent=null），前端选择器在 POSIX 上同样可用。
        return FilesystemListing(path="/", parent=None, directories=["/"])

    resolved = Path(path).resolve()
    if not resolved.is_dir():
        raise HTTPException(status_code=404, detail="dir_not_found")
    # 根目录（盘符根 / POSIX /）的 parent 等于自身 → null（面包屑到顶）。
    parent: str | None = (
        str(resolved.parent) if resolved.parent != resolved else None
    )
    return FilesystemListing(
        path=str(resolved),
        parent=parent,
        directories=_list_subdirectories(resolved),
        files=_list_files(resolved) if include_files else [],
    )


def _list_files(resolved: Path) -> list[str]:
    """列文件名（非目录项）；单条 OSError 跳过，scandir 整体失败 → 空表。"""
    files: list[str] = []
    try:
        with os.scandir(resolved) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        files.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return []
    return sorted(files, key=str.casefold)[:_MAX_ENTRIES]


__all__ = ["FilesystemListing", "router"]
