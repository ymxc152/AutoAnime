"""原子搬移与硬链接（E4b，硬需求：做种中替换文件，D9/D21）。

MoviePilot filemanager/Sonarr 的 hardlink 策略思想，代码自写（GPL 只学
思想）：下载目录原件不动（继续做种，D21），归档侧 hardlink 后原子改名
替换；跨盘/不支持 hardlink 时默认降级 copy（D9 拍板），``strict`` 策略
永不 copy；单文件超过 ``skip_over_bytes`` 跳过并记 audit。

规划（``plan_transfer``）与执行（``execute_transfer``）分离：规划是纯
决策可参数化单测，执行是文件 IO（服务层 to_thread 调用）。每条搬移都
带 ``reverse`` 数据（organize/rollback.py 执行反操作，与 E2 rollback
端点共用 audit reverse instruction 语义）。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from autoanime.organize.naming import subtitle_targets

CopyPolicy = Literal["allow", "strict"]
TransferKind = Literal["hardlink", "copy"]
Strategy = Literal["hardlink", "copy", "skip"]


@dataclass(frozen=True)
class PlannedMove:
    """一条搬移（视频本体或跟随字幕）。"""

    src: Path
    dst_name: str
    kind: TransferKind


@dataclass(frozen=True)
class TransferPlan:
    """一次归档搬移的完整计划（执行前可审计/可展示）。"""

    dst_dir: Path
    moves: tuple[PlannedMove, ...]
    strategy: Strategy
    skip_reason: str | None = None
    #: 反操作所需的原信息（execute 后写入 audit.reverse）
    reverse_moves: tuple[dict[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TransferResult:
    """执行结果（审计与回滚的依据）。"""

    dst_paths: tuple[Path, ...] = ()
    strategy: Strategy = "skip"
    error: str | None = None


def _same_filesystem(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def plan_transfer(
    video_src: Path,
    *,
    library_root: Path,
    dst_dir: Path,
    dst_name: str,
    siblings: list[Path] | None = None,
    copy_policy: CopyPolicy = "allow",
    skip_over_bytes: int = 20 * 1024**3,
    allow_replace_existing: bool = False,
) -> TransferPlan:
    """规划一次搬移（纯决策，不碰文件；参数化单测钉死）。

    - 源不存在 → skip（missing_source）；
    - 单文件超限 → skip（size_over_limit，D9）；
    - 目标位已存在：same content 幂等 skip；different content 默认 skip，
      只有洗版闸门显式放行（``allow_replace_existing=True``）才替换；
    - 同文件系统 → hardlink（原件保留做种，D21）；
    - 跨盘 → allow=copy（D9 默认降级）/ strict=skip（cross_fs_copy_disabled）。
    字幕跟随（D18）与视频同策略。
    """
    dst_dir = Path(dst_dir)
    if not video_src.exists():
        return TransferPlan(dst_dir=dst_dir, moves=(), strategy="skip", skip_reason="missing_source")
    size = video_src.stat().st_size
    if size > skip_over_bytes:
        return TransferPlan(
            dst_dir=dst_dir, moves=(), strategy="skip",
            skip_reason=f"size_over_limit:{size}",
        )
    dst = dst_dir / dst_name
    if dst.exists():
        try:
            same_content = video_src.samefile(dst)
        except OSError:
            same_content = False
        if same_content:
            return TransferPlan(
                dst_dir=dst_dir, moves=(), strategy="skip",
                skip_reason="dst-exists-same-content",
            )
        if not allow_replace_existing:
            return TransferPlan(
                dst_dir=dst_dir, moves=(), strategy="skip",
                skip_reason="dst-exists-upgrade-gated",
            )
    same_fs = _same_filesystem(video_src.parent if video_src.parent.exists() else video_src, library_root)
    if same_fs:
        kind: TransferKind = "hardlink"
        strategy: Strategy = "hardlink"
    elif copy_policy == "strict":
        return TransferPlan(
            dst_dir=dst_dir, moves=(), strategy="skip",
            skip_reason="cross_fs_copy_disabled",
        )
    else:
        kind = "copy"
        strategy = "copy"
    moves = [PlannedMove(src=video_src, dst_name=dst_name, kind=kind)]
    reverse: list[dict[str, str]] = [
        {
            "src": str(video_src),
            "dst": str(dst_dir / dst_name),
            "kind": kind,
            "role": "video",
        }
    ]
    for sub_src, sub_name in subtitle_targets(video_src, dst_name, siblings or []):
        moves.append(PlannedMove(src=sub_src, dst_name=sub_name, kind=kind))
        reverse.append(
            {
                "src": str(sub_src),
                "dst": str(dst_dir / sub_name),
                "kind": kind,
                "role": "subtitle",
            }
        )
    return TransferPlan(
        dst_dir=dst_dir,
        moves=tuple(moves),
        strategy=strategy,
        reverse_moves=tuple(reverse),
    )


def _execute_one(move: PlannedMove, dst_dir: Path) -> Path:
    """单文件 hardlink/copy：目标目录内先落临时名，再 os.replace 原子改名。"""
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / move.dst_name
    tmp = dst.with_name(f".{dst.name}.autoanime-tmp")
    if tmp.exists():
        tmp.unlink()
    if move.kind == "hardlink":
        os.link(move.src, tmp)
    else:
        shutil.copy2(move.src, tmp)
    os.replace(tmp, dst)  # 原子改名（同目录内 rename 原子性）
    return dst


def execute_transfer(plan: TransferPlan) -> TransferResult:
    """执行计划（文件 IO；服务层 to_thread 包裹）。失败时清理已落文件。"""
    if plan.strategy == "skip" or not plan.moves:
        return TransferResult(strategy=plan.strategy)
    done: list[Path] = []
    try:
        for move in plan.moves:
            done.append(_execute_one(move, plan.dst_dir))
    except OSError as exc:
        for path in done:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return TransferResult(strategy=plan.strategy, error=f"{type(exc).__name__}")
    return TransferResult(dst_paths=tuple(done), strategy=plan.strategy)


def replace_archive_file(old_dst: Path, new_plan: TransferPlan) -> TransferResult:
    """洗版替换（归档侧）：新文件先按计划落临时位，再原子顶替旧文件。

    旧文件是历史 hardlink：归档侧 unlink 只删链接名，下载原件继续做种
    （D21）。失败时新文件清理、旧文件原样保留（洗版回滚语义）。
    """
    if new_plan.strategy == "skip" or not new_plan.moves:
        return TransferResult(strategy=new_plan.strategy)
    staged: list[Path] = []
    try:
        for move in new_plan.moves:
            staged.append(_execute_one(move, new_plan.dst_dir))
    except OSError as exc:
        for path in staged:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return TransferResult(strategy=new_plan.strategy, error=f"{type(exc).__name__}")
    # 新文件已就位：顶替视频位（字幕跟随文件通常同名不同扩展，不顶替旧字幕）
    new_video = staged[0]
    try:
        if old_dst.exists() and old_dst != new_video:
            old_dst.unlink(missing_ok=True)
    except OSError as exc:
        # 旧文件删除失败不影响新文件生效；如实报告（B5 对账会跟进）。
        return TransferResult(dst_paths=tuple(staged), strategy=new_plan.strategy,
                              error=f"old_cleanup:{type(exc).__name__}")
    return TransferResult(dst_paths=tuple(staged), strategy=new_plan.strategy)
