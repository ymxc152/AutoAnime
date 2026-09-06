"""Pending 页（/api/pending）：待确认队列 + confirm/correct/reject。

学习流程全部走 memory 层既有方法（``learn_confirmation`` 学习三件套的
parse_memory+alias 部分、``MemoryGovernance.add_bypass`` 负记忆部分），
路由只做参数组装、状态落库与事件发布。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from autoanime.config import Settings
from autoanime.core.enums import MemorySource, PendingStatus, ResolvedBy
from autoanime.core.events import EventCategory
from autoanime.core.interfaces import ParseResult, RawName
from autoanime.core.models import PendingQueue
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.learn import StorageMemoryAccess, learn_confirmation
from autoanime.memory.store import SqliteStorage
from autoanime.organize import confirm_archive
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.web.deps import (
    ApiStoreDep,
    BusDep,
    GovernanceDep,
    PaginationDep,
    ReferenceChainDep,
    SettingsDep,
    StorageDep,
)
from autoanime.web.learning import (
    ACTION_PENDING_CONFIRM,
    ACTION_PENDING_CORRECT,
    ACTION_PENDING_REJECT,
    build_confirmed_result,
    pending_audit_row,
    publish,
)
from autoanime.web.queries import ApiStore
from autoanime.web.schemas import (
    Page,
    PendingConfirmIn,
    PendingCorrectIn,
    PendingOut,
    PendingRejectIn,
    PendingResolveOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pending", tags=["pending"])


async def _archive_after_resolution(
    row: PendingQueue,
    confirmed: ParseResult,
    *,
    settings: Settings,
    governance: MemoryGovernance,
    source: str,
) -> confirm_archive.ArchiveOutcome:
    """确认/纠正后的归档通路（报告 §6.1 v2 首要补齐项）。

    以确认结果 hardlink 入库（D17/D18/D9/D21 全套守卫，与 import/E4 同
    原语）；审计行与 import 同口径（episode.organized），import 重跑的
    already-archived 幂等桶据此放行。文件路径从 pending 行 context 的
    ``parent_path`` 还原；无上下文或文件已不在位时如实记原因——学习与
    resolve 不受影响。归档事件走 ORGANIZE 类别（Pipeline 页实时可见）。
    """
    context = row.context if isinstance(row.context, dict) else {}
    parent = context.get("parent_path")
    if not parent:
        return confirm_archive.ArchiveOutcome(archived=False, reason="no-source-file")
    file_path = Path(str(parent)) / row.raw_name
    outcome = await asyncio.to_thread(
        confirm_archive.archive_confirmed_release,
        file_path=file_path,
        result=confirmed,
        settings=settings,
        source=source,
    )
    try:
        await governance.record_audit(
            operation_id=uuid4().hex,
            entity="episode",
            action="episode.organized",
            instruction=confirm_archive.archive_audit_instruction(
                file_path=file_path, result=confirmed, outcome=outcome, source=source
            ),
            reverse={},
        )
    except Exception:  # noqa: BLE001 -- 审计失败不阻塞归档（与 ArchiveService 同口径）
        logger.warning("confirm archive audit write failed", exc_info=True)
    if outcome.archived:
        logger.info(
            "pending #%s archived via %s: %s", row.id, source, outcome.dst
        )
    return outcome


router = APIRouter(prefix="/pending", tags=["pending"])


async def _load_open_pending(store: ApiStore, pending_id: int) -> PendingQueue:
    row = await store.get_pending(pending_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"pending {pending_id} not found")
    if row.status is not PendingStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"pending {pending_id} already resolved (status={str(row.status)})",
        )
    return row


async def _l1_draft_title(raw_name: str) -> str | None:
    """重放 L1 草稿标题（纯函数解析，零外呼零落库）。

    学习时把 L1 草稿标题形状映射到确认标题形状（learn.draft_title，
    R3 验收跨集命中落地）——未来同风格兄弟集经 alias 读侧零外呼命中。
    """
    draft = await LocalRecognizer().parse(RawName(name=raw_name))
    return draft.title if draft else None


async def _learn(
    storage: SqliteStorage,
    reference_chain,
    *,
    row: PendingQueue,
    confirmed,
) -> tuple[int, bool]:
    """学习三件套（parse_memory 两级 + alias 回填）；返回 (写入条数, 是否 bypassed)。"""
    access = StorageMemoryAccess(storage)
    outcome = await learn_confirmation(
        access,
        confirmed=confirmed,
        raw_name=row.raw_name,
        source=MemorySource.MANUAL,
        bypass_lookup=access,
        reference_lookup=reference_chain,
        draft_title=await _l1_draft_title(row.raw_name),
    )
    return len(outcome.entries), outcome.bypassed


@router.get("", response_model=Page[PendingOut])
async def list_pending(
    store: ApiStoreDep,
    pagination: PaginationDep,
    status: str | None = None,
) -> Page[PendingOut]:
    rows, total = await store.list_pending_page(
        status=status, limit=pagination.limit, offset=pagination.offset
    )
    return Page(
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
        items=[PendingOut.model_validate(row) for row in rows],
    )


@router.post("/{pending_id}/confirm", response_model=PendingResolveOut)
async def confirm_pending(
    pending_id: int,
    store: ApiStoreDep,
    storage: StorageDep,
    bus: BusDep,
    governance: GovernanceDep,
    settings: SettingsDep,
    reference_chain: ReferenceChainDep,
    body: PendingConfirmIn | None = None,
) -> PendingResolveOut:
    """确认解析结论（字段缺省回退行内草稿）→ 学习（parse_memory+alias）。"""
    row = await _load_open_pending(store, pending_id)
    overrides = body.model_dump(exclude_none=True) if body is not None else {}
    try:
        confirmed = build_confirmed_result(row, overrides)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    learned, bypassed = await _learn(storage, reference_chain, row=row, confirmed=confirmed)
    archive = await _archive_after_resolution(
        row, confirmed, settings=settings, governance=governance, source="confirm"
    )
    resolution: dict[str, object] = {
        "action": "confirm",
        "confirmed_title": confirmed.title,
        "archive": archive.as_dict(),
    }
    audit = await store.resolve_pending(
        pending_id,
        status=PendingStatus.RESOLVED,
        resolution=resolution,
        resolved_by=ResolvedBy.MANUAL,
        audit_row=pending_audit_row(
            pending=row, action=ACTION_PENDING_CONFIRM, confirmed=confirmed
        ),
    )
    await publish(
        bus,
        category=EventCategory.PARSE,
        message="pending.confirmed",
        audit_id=audit.id if audit is not None else None,
        pending_id=pending_id,
        title=confirmed.title,
    )
    return PendingResolveOut(
        id=pending_id,
        status=PendingStatus.RESOLVED.value,
        resolution=resolution,
        resolved_by=ResolvedBy.MANUAL.value,
        learned_entries=learned,
        bypassed=bypassed,
    )


@router.post("/{pending_id}/correct", response_model=PendingResolveOut)
async def correct_pending(
    pending_id: int,
    body: PendingCorrectIn,
    store: ApiStoreDep,
    storage: StorageDep,
    governance: GovernanceDep,
    settings: SettingsDep,
    bus: BusDep,
    reference_chain: ReferenceChainDep,
) -> PendingResolveOut:
    """字段纠正（5.2 学习三件套：parse_memory + alias + bypass 负记忆）。"""
    row = await _load_open_pending(store, pending_id)
    overrides = body.model_dump(exclude_none=True)
    try:
        confirmed = build_confirmed_result(row, overrides)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    learned, bypassed = await _learn(storage, reference_chain, row=row, confirmed=confirmed)
    # 负记忆（5.3）：该 raw_name 的既有 L1/L2 结论已被人工推翻，登记 bypass。
    await governance.add_bypass(row.raw_name, reason=f"webui correct: pending #{pending_id}")
    archive = await _archive_after_resolution(
        row, confirmed, settings=settings, governance=governance, source="correct"
    )
    resolution: dict[str, object] = {
        "action": "correct",
        "confirmed_title": confirmed.title,
        "archive": archive.as_dict(),
    }
    audit = await store.resolve_pending(
        pending_id,
        status=PendingStatus.RESOLVED,
        resolution=resolution,
        resolved_by=ResolvedBy.MANUAL,
        audit_row=pending_audit_row(
            pending=row, action=ACTION_PENDING_CORRECT, confirmed=confirmed
        ),
    )
    await publish(
        bus,
        category=EventCategory.PARSE,
        message="pending.corrected",
        audit_id=audit.id if audit is not None else None,
        pending_id=pending_id,
        title=confirmed.title,
    )
    return PendingResolveOut(
        id=pending_id,
        status=PendingStatus.RESOLVED.value,
        resolution=resolution,
        resolved_by=ResolvedBy.MANUAL.value,
        learned_entries=learned,
        bypassed=True,
    )


@router.post("/{pending_id}/reject", response_model=PendingResolveOut)
async def reject_pending(
    pending_id: int,
    store: ApiStoreDep,
    bus: BusDep,
    body: PendingRejectIn | None = None,
) -> PendingResolveOut:
    """驳回：不学习、不落记忆，仅关闭队列项并留审计。"""
    row = await _load_open_pending(store, pending_id)
    reason = body.reason if body is not None else None
    resolution: dict[str, object] = {
        "action": "reject",
        "confirmed_title": str(row.context.get("title") or row.raw_name),
    }
    if reason is not None:
        resolution["reason"] = reason
    audit = await store.resolve_pending(
        pending_id,
        status=PendingStatus.SKIPPED,
        resolution=resolution,
        resolved_by=ResolvedBy.MANUAL,
        audit_row=pending_audit_row(pending=row, action=ACTION_PENDING_REJECT, confirmed=None),
    )
    await publish(
        bus,
        category=EventCategory.PARSE,
        message="pending.rejected",
        audit_id=audit.id if audit is not None else None,
        pending_id=pending_id,
    )
    return PendingResolveOut(
        id=pending_id,
        status=PendingStatus.SKIPPED.value,
        resolution=resolution,
        resolved_by=ResolvedBy.MANUAL.value,
        learned_entries=0,
        bypassed=False,
    )
