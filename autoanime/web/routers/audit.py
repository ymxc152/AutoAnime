"""Logs 页（/api/audit）：审计分页 + operation_id 分组视图。"""

from __future__ import annotations

from fastapi import APIRouter

from autoanime.web.deps import ApiStoreDep, PaginationDep
from autoanime.web.schemas import AuditOut, OperationGroupOut, Page

router = APIRouter(prefix="/audit", tags=["logs"])


@router.get("", response_model=Page[AuditOut])
async def list_audit(
    store: ApiStoreDep,
    pagination: PaginationDep,
    operation_id: str | None = None,
    entity: str | None = None,
    action: str | None = None,
) -> Page[AuditOut]:
    rows, total = await store.list_audit_page(
        operation_id=operation_id,
        entity=entity,
        action=action,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
        items=[AuditOut.model_validate(row) for row in rows],
    )


@router.get("/operations", response_model=Page[OperationGroupOut])
async def list_audit_operations(
    store: ApiStoreDep,
    pagination: PaginationDep,
) -> Page[OperationGroupOut]:
    groups, total = await store.list_audit_operations(
        limit=pagination.limit, offset=pagination.offset
    )
    return Page(
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
        items=[
            OperationGroupOut(
                operation_id=group.operation_id,
                rows=group.rows,
                entities=group.entities,
                actions=group.actions,
                first_audit_id=group.first_audit_id,
                last_audit_id=group.last_audit_id,
                rollbackable=group.rollbackable,
            )
            for group in groups
        ],
    )
