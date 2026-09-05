"""证据仲裁决策表——T1 定签名与结构，T4 填实现。

输入三方：L1 结果、L1+L2 融合结果（L2 内联融合不重构，orchestrator
先做融合）、L3 独立结果（可全为 ``None``）。逐字段仲裁，规则（PR5
统一契约）：

- R1 证据优先级 name > folder > context > memory > llm
  （未来 bangumi/tmdb 插在 memory 与 llm 之间）；
- R2 只补不覆盖；冲突记 audit；
- R3 缺失字段按优先级顺序补齐；
- R4 一致性升档：title+season 三方一致 → MEDIUM→HIGH；
- R5 验证升档：L1-None + 仅 LLM → MEDIUM；LLM title 与 L1 title shape
  归一化一致（``title_shape_matches``），或参考源（ReferenceFacts）
  验证一致 → HIGH；
- R6 L2 多季消歧：memory seasons 多值歧义 + L3/参考源 season 结论 →
  消歧回填；
- R7 L3 不可用 → 保持 L1/L2 原结果；
- R8 否决/冲突结论进 audit（operation_id 批次），不进 ParseResult。

本模块的判定函数体由 T4 实现；结构与常量（含 ``EVIDENCE_PRIORITY``、
``evidence_rank``、``title_shape_matches``）已定，T4 不得改签名。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.context import SOURCE_NONE
from autoanime.pipeline.l2.placeholders import build_title_shape
from autoanime.pipeline.l3.reference import ReferenceFacts

EVIDENCE_PRIORITY: tuple[str, ...] = ("name", "folder", "context", "memory", "llm")

#: 未来 bangumi/tmdb 参考源证据插在 memory 与 llm 之间（预留位）。
_FUTURE_REFERENCE_EVIDENCE = "reference"


@dataclass(frozen=True)
class ArbiterInput:
    """一次仲裁的全部输入（三方结果 + 上下文 + 参考事实 + 消歧输入）。"""

    raw: RawName
    l1_result: ParseResult | None
    fused: ParseResult | None
    l3_result: ParseResult | None
    context: ParseContext | None = None
    reference: ReferenceFacts | None = None
    #: memory 行的多值 seasons（>1 即歧义）；空表示无歧义或无 memory。
    memory_seasons: tuple[int, ...] = ()
    operation_id: str | None = None


@dataclass(frozen=True)
class FieldResolution:
    """单字段的仲裁结论：胜出值、胜出证据、是否发生冲突（R2 audit 用）。"""

    field: str
    value: str | int | Segment | None
    evidence: str | None
    conflict: bool = False


@dataclass(frozen=True)
class ArbiterAudit:
    """一条审计记录（R8）：只进 audit 批次，不进 ParseResult。"""

    action: str
    detail: dict[str, str | int | bool | None] = field(default_factory=dict)


@dataclass(frozen=True)
class ArbiterVerdict:
    """仲裁输出：最终 ParseResult（可为 ``None``，R7/L1-None+L3-None）+ audit。"""

    result: ParseResult | None
    audit: tuple[ArbiterAudit, ...] = ()


def evidence_rank(evidence: str | None) -> int:
    """证据来源的优先级序（小者优先）；未知或缺席（``none``）排最后。"""
    if evidence is None or evidence == SOURCE_NONE:
        return len(EVIDENCE_PRIORITY)
    try:
        return EVIDENCE_PRIORITY.index(evidence)
    except ValueError:
        return len(EVIDENCE_PRIORITY)


def title_shape_matches(left: str, right: str) -> bool:
    """两个 title 的归一化（title shape）一致性——R5 的比较基准。"""
    return build_title_shape(left) == build_title_shape(right)


def arbitrate(data: ArbiterInput) -> ArbiterVerdict:
    """决策表总入口：逐字段 R1-R3，整体 R4-R8。"""
    raise NotImplementedError("T4 implements the PR5 decision table")


def resolve_field(
    field: str,
    *,
    l1: ParseResult | None,
    fused: ParseResult | None,
    l3: ParseResult | None,
) -> FieldResolution:
    """单字段仲裁（R1/R2/R3）：按证据优先级取值、只补不覆盖、记录冲突。"""
    raise NotImplementedError("T4 implements R1-R3")


def upgrade_level(
    *,
    l1: ParseResult | None,
    fused: ParseResult,
    l3: ParseResult | None,
    reference: ReferenceFacts | None,
) -> Confidence:
    """R4/R5 升档：基于 fused 结果的一致性/验证升档，只升不降。"""
    raise NotImplementedError("T4 implements R4-R5")


def disambiguate_season(
    *,
    memory_seasons: tuple[int, ...],
    l3: ParseResult | None,
    reference: ReferenceFacts | None,
) -> int | None:
    """R6 多季消歧：memory seasons 多值歧义 + L3/参考源结论 → 唯一季。"""
    raise NotImplementedError("T4 implements R6")
