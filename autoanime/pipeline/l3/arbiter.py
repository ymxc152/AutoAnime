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

T4 实现语义（对 T1 留白处的契约解释，全部落在本模块内）：

- 逐字段仲裁把三方结果都视作候选来源：每个候选的优先级取自该结果
  自己的 per-field evidence（``evidence_rank``），同 rank 平手时按
  fused > l1 > l3 取更精炼的一方；fused 在正常管线下本就包含 L1 的
  贡献，l1 单独胜出只出现在 evidence 更高（如 name vs memory）时。
- 值缺席（``None`` 或空串）的来源不参与仲裁（R3 由其余来源补齐）。
- R4 的 title 一致按 title shape 归一化比较，season 按精确相等且
  三方均非 ``None``。
- R5 的「L1-None」有两种形态：L1 整体缺席（``l1_result=None``，结果
  仅 L3 来源，基础档位保底 MEDIUM），或 L1 存在但 season 无结论且
  season 仅由 L3 补齐。验证分支：L3 title 与 L1 title shape 一致，或
  与参考源 ``canonical_title`` 归一化一致，任一成立即升 HIGH。
- R6 中参考源结论 = ``ReferenceFacts.seasons`` 与歧义集合交集唯一；
  参考源优先于 L3（EVIDENCE_PRIORITY 中 reference 预留位高于 llm）。
- audit（R8）只随 ``ArbiterVerdict.audit`` 返回，持久化由 T5 编排接
  注入的窄 Protocol 完成；本模块是纯函数，不做任何 DB 写入。

本模块的判定函数体由 T4 实现；结构与常量（含 ``EVIDENCE_PRIORITY``、
``evidence_rank``、``title_shape_matches``）已定，T4 不得改签名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.confidence import confidence_for, missing_fields_for
from autoanime.pipeline.l1.context import SOURCE_NONE
from autoanime.pipeline.l2.placeholders import build_title_shape
from autoanime.pipeline.l3.reference import ReferenceFacts
from autoanime.pipeline.l3.schema import L3_EVIDENCE, L3_FIELDS

EVIDENCE_PRIORITY: tuple[str, ...] = ("name", "folder", "context", "memory", "llm")

#: 未来 bangumi/tmdb 参考源证据插在 memory 与 llm 之间（预留位）。
_FUTURE_REFERENCE_EVIDENCE = "reference"

#: 逐字段仲裁的字段集：ParseResult 契约中可仲裁的解析字段（与 L3 白名单一致）。
_ARBITRATED_FIELDS: tuple[str, ...] = L3_FIELDS

# --- R8 audit action 枚举 ---------------------------------------------------

AUDIT_FIELD_CONFLICT = "field_conflict"
AUDIT_LEVEL_UPGRADED = "level_upgraded"
AUDIT_SEASON_DISAMBIGUATED = "season_disambiguated"
AUDIT_SEASON_DISAMBIGUATION_REJECTED = "season_disambiguation_rejected"
AUDIT_L3_UNAVAILABLE = "l3_unavailable"

# --- 升档规则标识（audit detail 与内部判定共用） -----------------------------

RULE_R4_CONSISTENCY = "r4_consistency"
RULE_R5_VERIFIED = "r5_verified"
RULE_R5_BASE_MEDIUM = "r5_base_medium"


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
    base = data.fused if data.fused is not None else data.l1_result

    # R7：L3 不可用 → 保持 L1/L2 原结果（fused 优先，无 fused 用 l1）。
    if data.l3_result is None:
        return ArbiterVerdict(
            result=base,
            audit=(
                _with_operation(
                    AUDIT_L3_UNAVAILABLE, {"kept": _kept_route(data)}, data.operation_id
                ),
            ),
        )

    l3 = data.l3_result
    audits: list[ArbiterAudit] = []

    # R1-R3：逐字段仲裁；冲突（R2）记 audit。
    resolutions = {
        name: resolve_field(name, l1=data.l1_result, fused=data.fused, l3=l3)
        for name in _ARBITRATED_FIELDS
    }
    for name, resolution in resolutions.items():
        if resolution.conflict:
            audits.append(
                _with_operation(
                    AUDIT_FIELD_CONFLICT, _conflict_detail(name, data), data.operation_id
                )
            )

    # 组装最终字段值与证据：base 为底，按仲裁结论覆盖/补齐。
    if base is None:
        values: dict[str, object] = {name: None for name in _ARBITRATED_FIELDS}
        evidence: dict[str, str] = {}
    else:
        values = {
            "title": base.title,
            "season": base.season,
            "episode": base.episode,
            "segment": base.segment,
            "fansub": base.fansub,
        }
        evidence = dict(base.evidence)
    for name in _ARBITRATED_FIELDS:
        resolution = resolutions[name]
        if resolution.evidence is None or resolution.value is None:
            continue
        values[name] = resolution.value
        evidence[name] = resolution.evidence

    # R6：memory 多季消歧（回填或否决记 audit）。
    ambiguous = set(data.memory_seasons)
    if len(ambiguous) > 1:
        chosen = disambiguate_season(
            memory_seasons=data.memory_seasons, l3=l3, reference=data.reference
        )
        if chosen is not None:
            source = _disambiguation_source(data, ambiguous)
            values["season"] = chosen
            evidence["season"] = source
            audits.append(
                _with_operation(
                    AUDIT_SEASON_DISAMBIGUATED,
                    {
                        "chosen": chosen,
                        "source": source,
                        "memory_seasons": _seasons_text(data.memory_seasons),
                    },
                    data.operation_id,
                )
            )
        else:
            rejected = _rejected_season_candidate(data, ambiguous)
            if rejected is not None:
                value, source = rejected
                audits.append(
                    _with_operation(
                        AUDIT_SEASON_DISAMBIGUATION_REJECTED,
                        {
                            "candidate": value,
                            "source": source,
                            "memory_seasons": _seasons_text(data.memory_seasons),
                        },
                        data.operation_id,
                    )
                )

    # R4/R5：升档（基础档位：base 缺席时为 L3-only 的 MEDIUM）。
    base_level = base.level if base is not None else Confidence.MEDIUM
    result = _assemble(values, evidence, base_level)
    level = upgrade_level(l1=data.l1_result, fused=result, l3=l3, reference=data.reference)
    if level is not base_level:
        reasons = _upgrade_reasons(
            l1=data.l1_result, fused=result, l3=l3, reference=data.reference
        )
        audits.append(
            _with_operation(
                AUDIT_LEVEL_UPGRADED,
                {
                    "from": str(base_level),
                    "to": str(level),
                    "rules": "+".join(reasons),
                },
                data.operation_id,
            )
        )
        result = _assemble(values, evidence, level)

    return ArbiterVerdict(result=result, audit=tuple(audits))


def resolve_field(
    field: str,
    *,
    l1: ParseResult | None,
    fused: ParseResult | None,
    l3: ParseResult | None,
) -> FieldResolution:
    """单字段仲裁（R1/R2/R3）：按证据优先级取值、只补不覆盖、记录冲突。"""
    candidates = _candidates(field, l1=l1, fused=fused, l3=l3)
    if not candidates:
        return FieldResolution(field=field, value=None, evidence=None)
    ranked = sorted(candidates)
    _rank, _order, raw_value, evidence = ranked[0]
    # 候选值来自 ParseResult 字段，契约限定为 str | int | Segment。
    value = cast("str | int | Segment", raw_value)
    conflict = any(other[2] != raw_value for other in ranked[1:])
    return FieldResolution(field=field, value=value, evidence=evidence, conflict=conflict)


def upgrade_level(
    *,
    l1: ParseResult | None,
    fused: ParseResult,
    l3: ParseResult | None,
    reference: ReferenceFacts | None,
) -> Confidence:
    """R4/R5 升档：基于 fused 结果的一致性/验证升档，只升不降。"""
    level = fused.level
    reasons = _upgrade_reasons(l1=l1, fused=fused, l3=l3, reference=reference)
    if RULE_R5_BASE_MEDIUM in reasons:
        level = _raise_to_medium(level)
    if RULE_R4_CONSISTENCY in reasons:
        level = _raise_one(level)
    if RULE_R5_VERIFIED in reasons:
        level = _raise_one(level)
    return level


def disambiguate_season(
    *,
    memory_seasons: tuple[int, ...],
    l3: ParseResult | None,
    reference: ReferenceFacts | None,
) -> int | None:
    """R6 多季消歧：memory seasons 多值歧义 + L3/参考源结论 → 唯一季。"""
    ambiguous = set(memory_seasons)
    if len(ambiguous) <= 1:
        return None
    reference_season = _reference_season(reference, ambiguous)
    if reference_season is not None:
        return reference_season
    if l3 is not None and l3.season is not None and l3.season in ambiguous:
        return l3.season
    return None


# ---------------------------------------------------------------------------
# 内部纯函数
# ---------------------------------------------------------------------------


def _candidates(
    field: str, *, l1: ParseResult | None, fused: ParseResult | None, l3: ParseResult | None
) -> tuple[tuple[int, int, object, str], ...]:
    """字段的多来源候选 ``(rank, 平手序, 值, 证据)``；缺值/空串不参与。"""
    found: list[tuple[int, int, object, str]] = []
    for order, result in enumerate((fused, l1, l3)):
        if result is None:
            continue
        value = getattr(result, field)
        if value is None or value == "":
            continue
        evidence = result.evidence.get(field, SOURCE_NONE)
        found.append((evidence_rank(evidence), order, value, evidence))
    return tuple(found)


def _kept_route(data: ArbiterInput) -> str:
    """R7 保留路线：fused 优先，无 fused 用 l1，全无则 none。"""
    if data.fused is not None:
        return "fused"
    if data.l1_result is not None:
        return "l1"
    return "none"


def _with_operation(
    action: str, detail: dict[str, str | int | bool | None], operation_id: str | None
) -> ArbiterAudit:
    """audit 记录附带 operation_id 批次语义（缺席时不写该键）。"""
    if operation_id is not None:
        detail = {**detail, "operation_id": operation_id}
    return ArbiterAudit(action=action, detail=detail)


def _conflict_detail(field: str, data: ArbiterInput) -> dict[str, str | int | bool | None]:
    """R2 冲突 audit：记录该字段全部候选值与证据，不丢弃信息。"""
    detail: dict[str, str | int | bool | None] = {"field": field}
    for label, result in (("fused", data.fused), ("l1", data.l1_result), ("l3", data.l3_result)):
        if result is None:
            continue
        value = getattr(result, field)
        if value is None or value == "":
            continue
        detail[f"{label}_value"] = _detail_value(value)
        detail[f"{label}_evidence"] = result.evidence.get(field, SOURCE_NONE)
    return detail


def _detail_value(value: object) -> str | int:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (str, int)):
        return value
    return str(value)


def _seasons_text(seasons: tuple[int, ...]) -> str:
    return ",".join(str(season) for season in seasons)


def _reference_season(reference: ReferenceFacts | None, ambiguous: set[int]) -> int | None:
    """参考源的唯一 season 结论：与歧义集合交集恰好一个时成立。"""
    if reference is None:
        return None
    overlap = [season for season in dict.fromkeys(reference.seasons) if season in ambiguous]
    return overlap[0] if len(overlap) == 1 else None


def _disambiguation_source(data: ArbiterInput, ambiguous: set[int]) -> str:
    """R6 回填的证据来源：参考源优先于 L3（预留位高于 llm）。"""
    if _reference_season(data.reference, ambiguous) is not None:
        return _FUTURE_REFERENCE_EVIDENCE
    return L3_EVIDENCE


def _rejected_season_candidate(
    data: ArbiterInput, ambiguous: set[int]
) -> tuple[str | int, str] | None:
    """R6 否决 audit 的候选：结论存在但不在歧义集合内。"""
    l3 = data.l3_result
    if l3 is not None and l3.season is not None and l3.season not in ambiguous:
        return (l3.season, L3_EVIDENCE)
    reference = data.reference
    if reference is not None and reference.seasons:
        if not any(season in ambiguous for season in reference.seasons):
            return (_seasons_text(reference.seasons), _FUTURE_REFERENCE_EVIDENCE)
    return None


_LEVELS_DOWN: tuple[Confidence, ...] = (Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW)


def _raise_one(level: Confidence) -> Confidence:
    """向 HIGH 升一档；HIGH 保持 HIGH（只升不降）。"""
    index = _LEVELS_DOWN.index(level)
    return _LEVELS_DOWN[max(index - 1, 0)]


def _raise_to_medium(level: Confidence) -> Confidence:
    """R5 基础档位保底 MEDIUM（只升不降）。"""
    return Confidence.MEDIUM if level is Confidence.LOW else level


def _r4_consistent(l1: ParseResult, fused: ParseResult, l3: ParseResult) -> bool:
    """R4：三方 title（shape 归一化）一致且 season 结论一致（均非 None）。"""
    if fused.season is None or l1.season != fused.season or l3.season != fused.season:
        return False
    return title_shape_matches(l1.title, fused.title) and title_shape_matches(
        fused.title, l3.title
    )


def _l3_only(result: ParseResult) -> bool:
    """结果中所有已填字段证据均为 llm（仅 L3 来源）。"""
    for name in _ARBITRATED_FIELDS:
        value = getattr(result, name)
        if value is None or value == "":
            continue
        if result.evidence.get(name) != L3_EVIDENCE:
            return False
    return True


def _r5_eligible(
    *, l1: ParseResult | None, fused: ParseResult, l3: ParseResult | None
) -> bool:
    """R5 场景：L1 无 season 结论，缺失字段仅由 L3 补齐。"""
    if l3 is None:
        return False
    if l1 is None:
        return _l3_only(fused)
    return (
        l1.season is None
        and fused.season is not None
        and fused.evidence.get("season") == L3_EVIDENCE
    )


def _r5_verified(
    *, l1: ParseResult | None, l3: ParseResult | None, reference: ReferenceFacts | None
) -> bool:
    """R5 验证：L3 title 与参考源 canonical_title 或 L1 title shape 一致。"""
    if l3 is None:
        return False
    if reference is not None and reference.canonical_title:
        if title_shape_matches(l3.title, reference.canonical_title):
            return True
    return l1 is not None and title_shape_matches(l3.title, l1.title)


def _upgrade_reasons(
    *,
    l1: ParseResult | None,
    fused: ParseResult,
    l3: ParseResult | None,
    reference: ReferenceFacts | None,
) -> tuple[str, ...]:
    """本次升档触发的规则标识（``upgrade_level`` 与 audit 共用判定）。"""
    reasons: list[str] = []
    if l1 is None and _r5_eligible(l1=l1, fused=fused, l3=l3):
        reasons.append(RULE_R5_BASE_MEDIUM)
    if l1 is not None and l3 is not None and _r4_consistent(l1, fused, l3):
        reasons.append(RULE_R4_CONSISTENCY)
    if _r5_eligible(l1=l1, fused=fused, l3=l3) and _r5_verified(
        l1=l1, l3=l3, reference=reference
    ):
        reasons.append(RULE_R5_VERIFIED)
    return tuple(reasons)


def _as_optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value != "" else None


def _assemble(
    values: dict[str, object], evidence: dict[str, str], level: Confidence
) -> ParseResult:
    """把仲裁后的字段值/证据/档位组装成 ParseResult（重算缺失字段与置信分）。"""
    title = str(values["title"] or "")
    season = _as_optional_int(values["season"])
    episode = _as_optional_int(values["episode"])
    segment = values["segment"]
    if not isinstance(segment, Segment):
        # L1/L3 契约保证 segment 必填（L1Draft.to_parse_result / L3 schema）。
        segment = cast(Segment, segment)
    return ParseResult(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub=_as_optional_str(values["fansub"]),
        level=level,
        confidence=confidence_for(level),
        missing_fields=missing_fields_for(
            title=title, season=season, episode=episode, segment=segment
        ),
        evidence=dict(evidence),
    )
