"""Web 层组装辅助（E2）：确认/纠正的学习流程装配 + 事件发布。

「业务逻辑」全部走既有层：学习三件套 = ``memory.learn.learn_confirmation``
（parse_memory 两级 + alias 回填）与 ``MemoryGovernance.add_bypass``（负记忆）；
本模块只做参数组装（context 草稿 → ParseResult）与审计行/事件的构造。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from autoanime.core.enums import Confidence, Segment
from autoanime.core.events import Event, EventCategory, InMemoryEventBus
from autoanime.core.interfaces import ParseResult
from autoanime.core.models import AuditLog, PendingQueue

#: pending 行 context 里约定的草稿字段（识别管线写入时的契约键）。
_DRAFT_FIELDS = ("title", "season", "episode", "segment", "fansub", "folder", "parent_path")

ACTION_PENDING_CONFIRM = "pending_confirm"
ACTION_PENDING_CORRECT = "pending_correct"
ACTION_PENDING_REJECT = "pending_reject"
ACTION_ROLLBACK = "rollback"

ENTITY_PENDING_QUEUE = "pending_queue"

AUDIT_ENTITIES_PARSE = frozenset(
    {"parse_memory", "bypass_list", "arbiter", "pending_queue", "llm_cache"}
)
AUDIT_ENTITIES_ORGANIZE = frozenset({"episode", "release_record", "organize"})


def draft_fields_from_context(context: dict[str, object] | None) -> dict[str, Any]:
    """pending 行 context 中识别草稿字段（缺省回退源）。"""
    if not context:
        return {}
    return {key: context[key] for key in _DRAFT_FIELDS if key in context}


def build_confirmed_result(
    row: PendingQueue, overrides: dict[str, Any]
) -> ParseResult:
    """确认/纠正载荷 + 行内草稿合成权威 ParseResult（人工结论=HIGH）。

    优先级：请求体字段 > context 草稿。合成不出 title 时抛 ``ValueError``，
    由路由层转 422（学习需要 title 才能落 parse_memory/alias）。
    """
    draft = draft_fields_from_context(row.context)
    title = overrides.get("title") or draft.get("title")
    if not title or not str(title).strip():
        raise ValueError("confirmed title missing: no override and no draft title in context")
    segment_value = overrides.get("segment") or draft.get("segment") or Segment.EPISODE.value
    try:
        segment = Segment(str(segment_value))
    except ValueError:
        raise ValueError(f"unknown segment: {segment_value}") from None
    return ParseResult(
        title=str(title).strip(),
        season=_int_or_none(overrides.get("season") if overrides.get("season") is not None else draft.get("season")),
        episode=_int_or_none(
            overrides.get("episode") if overrides.get("episode") is not None else draft.get("episode")
        ),
        segment=segment,
        fansub=_str_or_none(
            overrides.get("fansub") if overrides.get("fansub") is not None else draft.get("fansub")
        ),
        # 人工确认/纠正的定义即受信输入（与 CLI confirm 同口径）。
        level=Confidence.HIGH,
        confidence=1.0,
        missing_fields=(),
        evidence={},
    )


def pending_audit_row(
    *,
    pending: PendingQueue,
    action: str,
    confirmed: ParseResult | None,
    operation_id: str | None = None,
) -> AuditLog:
    instruction: dict[str, object] = {"raw_name": pending.raw_name}
    if confirmed is not None:
        instruction["confirmed"] = {
            "title": confirmed.title,
            "season": confirmed.season,
            "episode": confirmed.episode,
            "segment": confirmed.segment.value,
            "fansub": confirmed.fansub,
        }
    return AuditLog(
        operation_id=operation_id or uuid4().hex,
        entity=ENTITY_PENDING_QUEUE,
        entity_id=pending.id,
        action=action,
        instruction=instruction,
        reverse={},
    )


async def publish(
    bus: InMemoryEventBus,
    *,
    category: EventCategory,
    message: str,
    audit_id: int | None = None,
    **payload_extra: object,
) -> None:
    """向进程内总线扇出一条事件；audit_id 进 payload 供 SSE 定 id。"""
    payload: dict[str, object] = dict(payload_extra)
    if audit_id is not None:
        payload["audit_id"] = audit_id
    await bus.publish(Event(category=category, message=message, payload=payload))


def audit_to_event(row: AuditLog) -> Event:
    """审计行 → SSE 事件（回放通道）：分类按实体映射，原样透传载荷。"""
    if row.entity in AUDIT_ENTITIES_PARSE:
        category = EventCategory.PARSE
    elif row.entity in AUDIT_ENTITIES_ORGANIZE:
        category = EventCategory.ORGANIZE
    else:
        category = EventCategory.SYSTEM
    return Event(
        category=category,
        message=f"{row.entity}.{row.action}",
        payload={
            "audit_id": row.id,
            "operation_id": row.operation_id,
            "entity": row.entity,
            "entity_id": row.entity_id,
            "action": row.action,
            "instruction": row.instruction,
            "actor": row.actor.value if hasattr(row.actor, "value") else str(row.actor),
        },
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
