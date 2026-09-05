"""撤销整理（POST /api/organize/{id}/rollback）：audit reverse instruction 执行 + 学习流程。

v1 执行引擎边界（诚实契约）：当前代码库唯一会产生的 ``reverse`` 形态是
governance 状态行的 ``{"status": <MemoryStatus>}``（回滚 = 恢复实体状态）；
organize 文件反操作（移动/改名的反向）由 E4 mover/rollback 落地后在
``organize/`` 扩展同一端点的执行引擎。E2 只支持 parse_memory 实体的状态
恢复，其余 reverse 键原样记入 ``skipped`` 并落审计，不静默丢弃。
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException

from autoanime.core.events import EventCategory
from autoanime.core.models import AuditLog
from autoanime.web.deps import ApiStoreDep, BusDep, GovernanceDep
from autoanime.web.learning import ACTION_ROLLBACK, publish
from autoanime.web.schemas import RollbackOut

router = APIRouter(prefix="/organize", tags=["organize"])

V1_REVERSIBLE_ENTITIES = frozenset({"parse_memory"})


def _split_reverse(
    entity: str, entity_id: int | None, reverse: dict[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    """执行 reverse 指令的 v1 子集；返回 (applied, skipped) 明细。"""
    applied: dict[str, object] = {}
    skipped: dict[str, object] = {}
    for key, value in reverse.items():
        if key == "status" and entity in V1_REVERSIBLE_ENTITIES and entity_id is not None:
            applied[key] = value
        else:
            skipped[key] = value
    return applied, skipped


@router.post("/{audit_id}/rollback", response_model=RollbackOut)
async def rollback_organize(
    audit_id: int,
    store: ApiStoreDep,
    governance: GovernanceDep,
    bus: BusDep,
) -> RollbackOut:
    row = await store.get_audit(audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
    reverse = dict(row.reverse or {})
    if not reverse:
        raise HTTPException(
            status_code=409,
            detail=f"audit row {audit_id} carries no reverse instruction; nothing to roll back",
        )

    applied, skipped = _split_reverse(row.entity, row.entity_id, reverse)
    if "status" in applied and row.entity_id is not None:
        restored = await store.restore_parse_memory_status(
            row.entity_id, str(applied["status"])
        )
        if not restored:
            # 实体行已不存在：状态恢复未生效，如实降级为 skipped。
            skipped["status"] = applied.pop("status")

    learned = False
    raw_name = row.instruction.get("raw_name") if row.instruction else None
    if isinstance(raw_name, str) and raw_name:
        # 学习流程（5.4）：回滚即登记错误模式，防同类 L1/L2 结论再次放行。
        await governance.add_bypass(raw_name, reason=f"rollback of audit #{audit_id}")
        learned = True

    rollback_row = AuditLog(
        operation_id=uuid4().hex,
        entity=row.entity,
        entity_id=row.entity_id,
        action=ACTION_ROLLBACK,
        instruction={
            "rolled_back_audit_id": audit_id,
            "applied": applied,
            "skipped": skipped,
        },
        reverse={"rollback_of": audit_id},
    )
    saved = await store.add_audit_row(rollback_row)
    await publish(
        bus,
        category=EventCategory.ORGANIZE,
        message="organize.rolled_back",
        audit_id=saved.id,
        rolled_back_audit_id=audit_id,
        learned=learned,
    )
    return RollbackOut(
        audit_id=saved.id,
        operation_id=saved.operation_id,
        applied={"applied": applied, "skipped": skipped},
        learned=learned,
    )
