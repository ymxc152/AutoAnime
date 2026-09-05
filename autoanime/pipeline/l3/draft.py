"""L3Draft → ParseResult 的构建纯函数。

- ``l3_parse_result``：独立的 L3 层结果（L1-None + 仅 LLM 的场景，R5
  定级 MEDIUM）；出现的每个字段 evidence 一律 ``llm``；
- ``apply_l3_draft``：把 L3 草稿并入既有结果——「不得覆盖 name/folder
  证据字段」的约束在此实现：LLM 证据优先级最低，只补空缺、永不覆盖
  已有值，且 evidence 为 ``name`` / ``folder`` 的字段即使空缺也不补
  （与 L2 memory 融合同一保护集）。冲突检测与 audit（R2/R8）、升档
  （R4/R5）都归 arbiter（T4）；本模块合并时保持 base level 不变。

纯函数。
"""

from __future__ import annotations

from autoanime.core.enums import Confidence
from autoanime.core.interfaces import ParseResult
from autoanime.pipeline.l1.confidence import confidence_for, missing_fields_for
from autoanime.pipeline.l1.context import SOURCE_FOLDER, SOURCE_NAME
from autoanime.pipeline.l3.schema import L3_EVIDENCE, L3_FIELDS, L3Draft

_PROTECTED_EVIDENCE = frozenset({SOURCE_NAME, SOURCE_FOLDER})


def l3_parse_result(draft: L3Draft) -> ParseResult:
    """把 L3 草稿建成独立 ParseResult：level MEDIUM，evidence 全 ``llm``。"""
    fields: dict[str, object] = {
        "title": draft.title,
        "season": draft.season,
        "episode": draft.episode,
        "segment": draft.segment,
        "fansub": draft.fansub,
    }
    evidence = {
        name: L3_EVIDENCE
        for name in L3_FIELDS
        if fields[name] is not None and fields[name] != ""
    }
    title = draft.title
    season = draft.season
    episode = draft.episode
    segment = draft.segment
    return ParseResult(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub=draft.fansub,
        level=Confidence.MEDIUM,
        confidence=confidence_for(Confidence.MEDIUM),
        missing_fields=missing_fields_for(
            title=title, season=season, episode=episode, segment=segment
        ),
        evidence=evidence,
    )


def apply_l3_draft(result: ParseResult | None, draft: L3Draft) -> ParseResult:
    """把 L3 草稿并入既有结果；``result=None`` 等价独立构建。

    只补空缺（值缺失且证据非 name/folder）；任何已存在的值都不覆盖。
    base level 保持不变——升档是 arbiter 的决定。
    """
    if result is None:
        return l3_parse_result(draft)

    evidence = dict(result.evidence)
    title = result.title
    season = result.season
    episode = result.episode
    segment = result.segment
    fansub = result.fansub

    if draft.title and _fillable(result.title, evidence, "title"):
        title = draft.title
        evidence["title"] = L3_EVIDENCE
    if draft.season is not None and _fillable(result.season, evidence, "season"):
        season = draft.season
        evidence["season"] = L3_EVIDENCE
    if draft.episode is not None and _fillable(result.episode, evidence, "episode"):
        episode = draft.episode
        evidence["episode"] = L3_EVIDENCE
    if draft.segment is not None and _fillable(result.segment, evidence, "segment"):
        segment = draft.segment
        evidence["segment"] = L3_EVIDENCE
    if draft.fansub is not None and _fillable(result.fansub, evidence, "fansub"):
        fansub = draft.fansub
        evidence["fansub"] = L3_EVIDENCE

    missing = missing_fields_for(title=title, season=season, episode=episode, segment=segment)
    return ParseResult(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub=fansub,
        level=result.level,
        confidence=confidence_for(result.level),
        missing_fields=missing,
        evidence=evidence,
    )


def _fillable(current: object, evidence: dict[str, str], field_name: str) -> bool:
    """空缺且证据不受保护：LLM 可以补；其余一律不动。"""
    if current is None or current == "":
        return evidence.get(field_name) not in _PROTECTED_EVIDENCE
    return False
