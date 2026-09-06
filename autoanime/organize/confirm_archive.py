"""确认/纠正后的归档通路（报告 §6.1 v2 首要补齐项，2026-09-06 拍板落地）。

此前 confirm/correct 只学习不归档：MEDIUM 文件确认后要删除重导或走订阅
路径入库。本模块给出 CLI（``cli._confirm``）与 WebUI（``pending`` 路由的
confirm/correct）共用的单一实现：以确认结果走与 import/E4 同一套
naming + mover 原语（D17 命名 + D18 字幕跟随 + D9 同盘判定 + D21 目标位
守卫），审计行经 :func:`archive_audit_instruction` 保持与 import 同口径
（``episode.organized``，``instruction["file"]`` = 源文件名，import 重跑
幂等桶据此放行）。

护栏（全部如实返回原因，不静默、不抛错阻断确认主流程）：
- 文件不存在/已移走 → ``file-missing``（学习照常生效，归档跳过）；
- 目标位已占用：同 inode → ``dst-exists-same-content``（内容已在库），
  不同 inode → ``dst-exists-upgrade-gated``（版本替换属洗版闸门管辖）；
- 单文件超限 / 跨盘 strict → plan_transfer 的 skip 原因原样透出；
- 执行失败 → mover 错误消息透出。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from autoanime.config import Settings
from autoanime.core.enums import Segment
from autoanime.core.interfaces import ParseResult
from autoanime.organize import mover
from autoanime.organize.naming import NamingInput, relative_path

logger = logging.getLogger(__name__)

__all__ = ["ArchiveOutcome", "archive_audit_instruction", "archive_confirmed_release"]


@dataclass(frozen=True)
class ArchiveOutcome:
    """归档通路结果（如实字段；``archived=False`` 时 ``reason`` 必有）。"""

    archived: bool
    dst: str | None = None
    strategy: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        """resolution 字典嵌入形态（WebUI resolution / CLI JSON 共用）。"""
        payload: dict[str, object] = {"archived": self.archived}
        if self.dst is not None:
            payload["dst"] = self.dst
        if self.strategy is not None:
            payload["strategy"] = self.strategy
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


def archive_audit_instruction(
    *,
    file_path: Path,
    result: ParseResult,
    outcome: ArchiveOutcome,
    source: str = "confirm",
) -> dict[str, object]:
    """审计 instruction（与 ``cli._import_audit`` / E4 同口径）。

    ``instruction["file"]`` 保持源文件名——import 重跑的 already-archived
    幂等桶按它放行。归档未发生时 ``dst``/``strategy`` 缺席、``reason`` 记
    跳过原因（审计如实，不静默）。
    """
    instruction: dict[str, object] = {
        "file": file_path.name,
        "source": source,
        "title": result.title,
        "season": result.season,
        "episode": result.episode,
    }
    if outcome.dst is not None:
        instruction["dst"] = outcome.dst
    if outcome.strategy is not None:
        instruction["strategy"] = outcome.strategy
    if outcome.reason is not None:
        instruction["reason"] = outcome.reason
    return instruction


def archive_confirmed_release(
    *,
    file_path: Path,
    result: ParseResult,
    settings: Settings,
    source: str = "confirm",
) -> ArchiveOutcome:
    """把确认/纠正结果落成归档（同步文件 IO；调用方在 async 上下文用
    ``asyncio.to_thread`` 包裹）。"""
    if not file_path.exists():
        return ArchiveOutcome(archived=False, reason="file-missing")
    # 归档目标根先就位（D9 同盘判定需要 stat 到 library 根）。
    try:
        Path(settings.library_path).mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("confirm archive: library root not preparable: %s", settings.library_path)

    try:
        plan = _confirmed_archive_plan(file_path, result, settings)
    except OSError as exc:
        # plan_transfer 的 stat 在 exists/stat 竞态下可能抛 OSError：
        # 如实降级为归档跳过（学习已生效，重导可自愈），不击穿调用方。
        logger.warning("confirm archive plan failed: %s", exc)
        return ArchiveOutcome(archived=False, reason=f"plan-failed:{type(exc).__name__}")
    if plan.strategy == "skip":
        return ArchiveOutcome(archived=False, reason=plan.skip_reason or "skipped")
    if plan.moves:
        dst_path = plan.dst_dir / plan.moves[0].dst_name
        if dst_path.exists():
            try:
                same_content = dst_path.samefile(file_path)
            except OSError:
                same_content = False
            # D21 守卫（与 import 同口径）：库内替换只能走洗版评分闸门。
            reason = "dst-exists-same-content" if same_content else "dst-exists-upgrade-gated"
            return ArchiveOutcome(archived=False, reason=reason)
    try:
        executed = mover.execute_transfer(plan)
    except OSError as exc:
        logger.warning("confirm archive execute failed: %s", exc)
        return ArchiveOutcome(archived=False, reason=f"transfer-error:{type(exc).__name__}")
    if executed.error is not None or not executed.dst_paths:
        return ArchiveOutcome(archived=False, reason=executed.error or "transfer failed")
    return ArchiveOutcome(
        archived=True,
        dst=str(executed.dst_paths[0]),
        strategy=executed.strategy,
    )


def _confirmed_archive_plan(
    file_path: Path, result: ParseResult, settings: Settings
) -> mover.TransferPlan:
    """确认结果的归档计划：与 CLI import 的 ``_archive_plan`` 同一原语
    （naming 三槽同填，由 ``naming_title_language`` 回退链取用）。"""
    media_type = "movie" if result.segment is Segment.MOVIE else "tv"
    naming = NamingInput(
        title_cn=result.title,
        title_romaji=result.title,
        title_jp=result.title,
        season_number=result.season or 1,
        episode_number=result.episode or 0,
        media_type=media_type,
        release_title=file_path.name,
    )
    rel = relative_path(
        naming, language=settings.naming_title_language, extension=file_path.suffix.lower()
    )
    library_root = Path(settings.library_path)
    siblings = (
        [child for child in file_path.parent.iterdir() if child.is_file()]
        if file_path.parent.exists()
        else []
    )
    return mover.plan_transfer(
        file_path,
        library_root=library_root,
        dst_dir=library_root / rel.parent,
        dst_name=rel.name,
        siblings=siblings,
        copy_policy="strict" if settings.upgrade_copy_policy == "strict" else "allow",
        skip_over_bytes=int(settings.upgrade_skip_size_gb * 1024**3),
    )
