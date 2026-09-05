"""PR5 T4：arbiter 仲裁决策表的单元测试（全部离线）。

覆盖决策表 R1-R8 的来源×字段×一致/冲突/None 组合矩阵、R4/R5 升档、
R6 多季消歧（L3 与参考源旁证）、R7 优雅降级与 R8 audit 语义。
"""

from __future__ import annotations

from typing import Any

import pytest

from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseResult, RawName
from autoanime.pipeline.l3.arbiter import (
    AUDIT_FIELD_CONFLICT,
    AUDIT_L3_UNAVAILABLE,
    AUDIT_LEVEL_UPGRADED,
    AUDIT_SEASON_DISAMBIGUATED,
    AUDIT_SEASON_DISAMBIGUATION_REJECTED,
    RULE_R4_CONSISTENCY,
    RULE_R5_BASE_MEDIUM,
    RULE_R5_VERIFIED,
    ArbiterInput,
    arbitrate,
    disambiguate_season,
    resolve_field,
    upgrade_level,
)
from autoanime.pipeline.l3.reference import ReferenceFacts


def make_result(
    *,
    title: str = "Sousou no Frieren",
    season: int | None = 1,
    episode: int | None = 5,
    segment: Segment | None = Segment.EPISODE,
    fansub: str | None = None,
    level: Confidence = Confidence.MEDIUM,
    evidence: dict[str, str] | None = None,
) -> ParseResult:
    """构造测试用 ParseResult；默认 evidence 与已填字段对齐。"""
    if evidence is None:
        evidence = {}
        if title:
            evidence["title"] = "name"
        if season is not None:
            evidence["season"] = "name"
        if episode is not None:
            evidence["episode"] = "name"
        if segment is not None:
            evidence["segment"] = "name"
        if fansub is not None:
            evidence["fansub"] = "name"
    return ParseResult(
        title=title,
        season=season,
        episode=episode,
        segment=segment if segment is not None else Segment.EPISODE,
        fansub=fansub,
        level=level,
        confidence={Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.6, Confidence.LOW: 0.2}[level],
        evidence=evidence,
    )


def make_input(**overrides: Any) -> ArbiterInput:
    defaults: dict[str, Any] = {
        "raw": RawName(name="[SubGroup] Sousou no Frieren - 05 [1080p].mkv"),
        "l1_result": None,
        "fused": None,
        "l3_result": None,
    }
    defaults.update(overrides)
    return ArbiterInput(**defaults)


def audits_of(verdict: Any, action: str) -> list[Any]:
    return [audit for audit in verdict.audit if audit.action == action]


def detail_of(verdict: Any, action: str) -> dict[str, Any]:
    found = audits_of(verdict, action)
    assert len(found) == 1, f"expected exactly one {action} audit, got {len(found)}"
    return found[0].detail


# ---------------------------------------------------------------------------
# R1/R2/R3: resolve_field 逐字段仲裁矩阵
# ---------------------------------------------------------------------------


def test_resolve_field_no_sources_gives_empty_resolution() -> None:
    resolution = resolve_field("season", l1=None, fused=None, l3=None)

    assert resolution.value is None
    assert resolution.evidence is None
    assert resolution.conflict is False


@pytest.mark.parametrize(
    ("slot", "evidence"),
    [
        ("l1", "name"),
        ("l1", "folder"),
        ("fused", "context"),
        ("fused", "memory"),
        ("l3", "llm"),
    ],
)
def test_resolve_field_single_source_wins(slot: str, evidence: str) -> None:
    result = make_result(season=2, evidence={"season": evidence})
    args: dict[str, ParseResult | None] = {"l1": None, "fused": None, "l3": None}
    args[slot] = result

    resolution = resolve_field("season", **args)

    assert resolution.value == 2
    assert resolution.evidence == evidence
    assert resolution.conflict is False


def test_resolve_field_l3_only_source() -> None:
    l3 = make_result(season=2, evidence={"season": "llm", "title": "llm"})

    resolution = resolve_field("season", l1=None, fused=None, l3=l3)

    assert resolution.value == 2
    assert resolution.evidence == "llm"
    assert resolution.conflict is False


def test_resolve_field_high_priority_beats_llm_and_records_conflict() -> None:
    fused = make_result(season=1, evidence={"season": "memory"})
    l3 = make_result(season=2, evidence={"season": "llm"})

    resolution = resolve_field("season", l1=None, fused=fused, l3=l3)

    assert resolution.value == 1
    assert resolution.evidence == "memory"
    assert resolution.conflict is True


def test_resolve_field_l1_name_beats_fused_memory_across_results() -> None:
    l1 = make_result(title="Frieren", evidence={"title": "name"})
    fused = make_result(title="葬送のフリーレン", evidence={"title": "memory"})

    resolution = resolve_field("title", l1=l1, fused=fused, l3=None)

    assert resolution.value == "Frieren"
    assert resolution.evidence == "name"
    assert resolution.conflict is True


_SLOT_PREFERENCE: dict[str, tuple[str, str, str]] = {
    "name": ("l1", "fused", "l3"),
    "folder": ("l1", "fused", "l3"),
    "context": ("fused", "l1", "l3"),
    "memory": ("fused", "l1", "l3"),
    "llm": ("l3", "fused", "l1"),
}


def _place(evidence: str, result: ParseResult, args: dict[str, ParseResult | None]) -> None:
    """把候选放进它语义上所属的结果槽；槽被占用时依次退让。"""
    for slot in _SLOT_PREFERENCE[evidence]:
        if args[slot] is None:
            args[slot] = result
            return
    raise AssertionError(f"no free slot for evidence {evidence}")


@pytest.mark.parametrize(
    ("winner_evidence", "loser_evidence"),
    [
        ("name", "folder"),
        ("folder", "context"),
        ("context", "memory"),
        ("memory", "llm"),
    ],
)
def test_resolve_field_evidence_priority_pairs(winner_evidence: str, loser_evidence: str) -> None:
    winner = make_result(season=1, evidence={"season": winner_evidence})
    loser = make_result(season=2, evidence={"season": loser_evidence})
    args: dict[str, ParseResult | None] = {"l1": None, "fused": None, "l3": None}
    _place(winner_evidence, winner, args)
    _place(loser_evidence, loser, args)

    resolution = resolve_field("season", **args)

    assert resolution.evidence == winner_evidence
    assert resolution.value == 1
    assert resolution.conflict is True


def test_resolve_field_agreeing_sources_do_not_conflict() -> None:
    l1 = make_result(season=1, evidence={"season": "name"})
    fused = make_result(season=1, evidence={"season": "name"})
    l3 = make_result(season=1, evidence={"season": "llm"})

    resolution = resolve_field("season", l1=l1, fused=fused, l3=l3)

    assert resolution.value == 1
    assert resolution.evidence == "name"
    assert resolution.conflict is False


def test_resolve_field_missing_high_priority_filled_by_llm() -> None:
    fused = make_result(season=None, evidence={"title": "name"})
    l3 = make_result(season=3, evidence={"season": "llm", "title": "llm"})

    resolution = resolve_field("season", l1=None, fused=fused, l3=l3)

    assert resolution.value == 3
    assert resolution.evidence == "llm"
    assert resolution.conflict is False


def test_resolve_field_empty_string_value_is_absent() -> None:
    fused = make_result(fansub="", evidence={"fansub": "name", "title": "name"})
    l3 = make_result(fansub="LoliHouse", evidence={"fansub": "llm", "title": "llm"})

    resolution = resolve_field("fansub", l1=None, fused=fused, l3=l3)

    assert resolution.value == "LoliHouse"
    assert resolution.evidence == "llm"


def test_resolve_field_same_value_from_fused_and_l1_takes_fused() -> None:
    l1 = make_result(season=1, evidence={"season": "name"})
    fused = make_result(season=1, evidence={"season": "name"})

    resolution = resolve_field("season", l1=l1, fused=fused, l3=None)

    assert resolution.value == 1
    assert resolution.evidence == "name"
    assert resolution.conflict is False


# ---------------------------------------------------------------------------
# R4/R5: upgrade_level 只升不降
# ---------------------------------------------------------------------------


def test_upgrade_level_r4_three_way_agreement_raises_medium_to_high() -> None:
    l1 = make_result(title="Sousou no Frieren", season=1, level=Confidence.MEDIUM)
    fused = make_result(title="Sousou no Frieren", season=1, level=Confidence.MEDIUM)
    l3 = make_result(title="sousou no frieren", season=1, evidence={"title": "llm"})

    level = upgrade_level(l1=l1, fused=fused, l3=l3, reference=None)

    assert level is Confidence.HIGH


def test_upgrade_level_r4_raises_low_one_step() -> None:
    l1 = make_result(title="Frieren", season=1, level=Confidence.LOW)
    fused = make_result(title="Frieren", season=1, level=Confidence.LOW)
    l3 = make_result(title="Frieren", season=1, evidence={"title": "llm"})

    level = upgrade_level(l1=l1, fused=fused, l3=l3, reference=None)

    assert level is Confidence.MEDIUM


def test_upgrade_level_r4_high_stays_high() -> None:
    l1 = make_result(title="Frieren", season=1, level=Confidence.HIGH)
    fused = make_result(title="Frieren", season=1, level=Confidence.HIGH)
    l3 = make_result(title="Frieren", season=1, evidence={"title": "llm"})

    level = upgrade_level(l1=l1, fused=fused, l3=l3, reference=None)

    assert level is Confidence.HIGH


@pytest.mark.parametrize(
    ("l1_season", "fused_season", "l3_season", "l3_title"),
    [
        (1, 1, 2, "Sousou no Frieren"),  # season 不一致
        (1, 2, 1, "Sousou no Frieren"),  # fused season 偏离
        (1, 1, 1, "Spy x Family"),  # title shape 不一致
    ],
)
def test_upgrade_level_r4_not_fired_on_disagreement(
    l1_season: int, fused_season: int, l3_season: int, l3_title: str
) -> None:
    l1 = make_result(title="Sousou no Frieren", season=l1_season)
    fused = make_result(title="Sousou no Frieren", season=fused_season)
    l3 = make_result(title=l3_title, season=l3_season, evidence={"title": "llm"})

    assert upgrade_level(l1=l1, fused=fused, l3=l3, reference=None) is Confidence.MEDIUM


def test_upgrade_level_r4_needs_all_three_results() -> None:
    fused = make_result(title="Frieren", season=1)
    l3 = make_result(title="Frieren", season=1, evidence={"title": "llm"})

    assert upgrade_level(l1=None, fused=fused, l3=l3, reference=None) is Confidence.MEDIUM
    assert (
        upgrade_level(l1=fused, fused=fused, l3=None, reference=None) is Confidence.MEDIUM
    )


def test_upgrade_level_r5_l1_none_l3_only_reference_confirms() -> None:
    fused = make_result(
        title="Sousou no Frieren",
        season=1,
        episode=None,
        evidence={"title": "llm", "season": "llm", "segment": "llm"},
    )
    l3 = make_result(
        title="Sousou no Frieren", season=1, episode=None, evidence={"title": "llm"}
    )
    reference = ReferenceFacts(canonical_title="sousou no frieren", seasons=(1,))

    level = upgrade_level(l1=None, fused=fused, l3=l3, reference=reference)

    assert level is Confidence.HIGH


def test_upgrade_level_r5_l1_none_l3_only_reference_mismatch_stays_medium() -> None:
    fused = make_result(
        title="Sousou no Frieren",
        season=1,
        episode=None,
        evidence={"title": "llm", "season": "llm", "segment": "llm"},
    )
    l3 = make_result(
        title="Sousou no Frieren", season=1, episode=None, evidence={"title": "llm"}
    )
    reference = ReferenceFacts(canonical_title="Spy x Family", seasons=(1,))

    assert upgrade_level(l1=None, fused=fused, l3=l3, reference=reference) is Confidence.MEDIUM
    assert upgrade_level(l1=None, fused=fused, l3=l3, reference=None) is Confidence.MEDIUM


def test_upgrade_level_r5_l1_none_l3_only_low_gets_medium_floor() -> None:
    fused = make_result(
        title="Frieren",
        season=None,
        episode=None,
        segment=Segment.EPISODE,
        level=Confidence.LOW,
        evidence={"title": "llm", "segment": "llm"},
    )
    l3 = make_result(
        title="Frieren",
        season=None,
        episode=None,
        level=Confidence.LOW,
        evidence={"title": "llm"},
    )

    assert upgrade_level(l1=None, fused=fused, l3=l3, reference=None) is Confidence.MEDIUM


def test_upgrade_level_r5_l1_season_none_llm_fills_and_title_shape_confirms() -> None:
    l1 = make_result(title="Sousou no Frieren", season=None, episode=5)
    fused = make_result(
        title="Sousou no Frieren",
        season=1,
        episode=5,
        evidence={"title": "name", "season": "llm", "episode": "name", "segment": "name"},
    )
    l3 = make_result(title="sousou  no frieren", season=1, evidence={"title": "llm"})

    level = upgrade_level(l1=l1, fused=fused, l3=l3, reference=None)

    assert level is Confidence.HIGH


def test_upgrade_level_r5_not_eligible_when_l1_already_concluded_season() -> None:
    l1 = make_result(title="Sousou no Frieren", season=1)
    fused = make_result(
        title="Sousou no Frieren",
        season=1,
        evidence={"title": "name", "season": "name", "segment": "name"},
    )
    l3 = make_result(title="Sousou no Frieren", season=1, evidence={"title": "llm"})

    # R4 已覆盖三方一致场景；R5 不应叠加（此处仅验证 R5 不走 title shape 分支）。
    assert upgrade_level(l1=l1, fused=fused, l3=l3, reference=None) is Confidence.HIGH


def test_upgrade_level_r5_title_shape_branch_needs_l1_or_reference() -> None:
    l1 = make_result(title="Completely Different", season=None)
    fused = make_result(
        title="Sousou no Frieren",
        season=1,
        evidence={"title": "llm", "season": "llm", "segment": "llm"},
    )
    l3 = make_result(title="Sousou no Frieren", season=1, evidence={"title": "llm"})

    # L1 在场但 title 不匹配、无参考源 → 不升。
    assert upgrade_level(l1=l1, fused=fused, l3=l3, reference=None) is Confidence.MEDIUM


def test_upgrade_level_r5_reference_confirms_even_with_mismatching_l1() -> None:
    l1 = make_result(title="Noise Title", season=None)
    fused = make_result(
        title="Sousou no Frieren",
        season=1,
        episode=None,
        evidence={"title": "llm", "season": "llm", "segment": "llm"},
    )
    l3 = make_result(
        title="Sousou no Frieren", season=1, episode=None, evidence={"title": "llm"}
    )
    reference = ReferenceFacts(canonical_title="sousou no frieren", seasons=(1,))

    assert upgrade_level(l1=l1, fused=fused, l3=l3, reference=reference) is Confidence.HIGH


# ---------------------------------------------------------------------------
# R6: disambiguate_season
# ---------------------------------------------------------------------------


def test_disambiguate_season_no_ambiguity_returns_none() -> None:
    l3 = make_result(season=1, evidence={"season": "llm"})

    assert disambiguate_season(memory_seasons=(1,), l3=l3, reference=None) is None
    assert disambiguate_season(memory_seasons=(), l3=l3, reference=None) is None


def test_disambiguate_season_l3_conclusion_inside_set() -> None:
    l3 = make_result(season=2, evidence={"season": "llm"})

    assert disambiguate_season(memory_seasons=(1, 2), l3=l3, reference=None) == 2


def test_disambiguate_season_reference_unique_overlap_wins_over_l3() -> None:
    l3 = make_result(season=2, evidence={"season": "llm"})
    reference = ReferenceFacts(canonical_title="Frieren", seasons=(1,))

    assert disambiguate_season(memory_seasons=(1, 2), l3=l3, reference=reference) == 1


def test_disambiguate_season_reference_multi_overlap_is_no_conclusion() -> None:
    reference = ReferenceFacts(canonical_title="Frieren", seasons=(1, 2))

    assert disambiguate_season(memory_seasons=(1, 2), l3=None, reference=reference) is None


def test_disambiguate_season_l3_outside_set_returns_none() -> None:
    l3 = make_result(season=3, evidence={"season": "llm"})

    assert disambiguate_season(memory_seasons=(1, 2), l3=l3, reference=None) is None


def test_disambiguate_season_without_conclusions_returns_none() -> None:
    assert disambiguate_season(memory_seasons=(1, 2), l3=None, reference=None) is None
    l3 = make_result(season=None, evidence={"season": "llm"})
    assert disambiguate_season(memory_seasons=(1, 2), l3=l3, reference=None) is None


# ---------------------------------------------------------------------------
# R7: 优雅降级
# ---------------------------------------------------------------------------


def test_arbitrate_l3_none_keeps_fused() -> None:
    fused = make_result()

    verdict = arbitrate(make_input(fused=fused))

    assert verdict.result is fused
    detail = detail_of(verdict, AUDIT_L3_UNAVAILABLE)
    assert detail["kept"] == "fused"


def test_arbitrate_l3_none_keeps_l1_when_no_fused() -> None:
    l1 = make_result()

    verdict = arbitrate(make_input(l1_result=l1))

    assert verdict.result is l1
    assert detail_of(verdict, AUDIT_L3_UNAVAILABLE)["kept"] == "l1"


def test_arbitrate_all_none_returns_none_verdict() -> None:
    verdict = arbitrate(make_input())

    assert verdict.result is None
    assert detail_of(verdict, AUDIT_L3_UNAVAILABLE)["kept"] == "none"


# ---------------------------------------------------------------------------
# arbitrate 端到端：字段仲裁 + 升档 + audit
# ---------------------------------------------------------------------------


def test_arbitrate_l3_fills_missing_season_and_confirms_via_title_shape() -> None:
    l1 = make_result(title="Sousou no Frieren", season=None, episode=5)
    fused = make_result(
        title="Sousou no Frieren",
        season=None,
        episode=5,
        evidence={"title": "name", "episode": "name", "segment": "name"},
    )
    l3 = make_result(
        title="Sousou no Frieren",
        season=1,
        episode=5,
        fansub="LoliHouse",
        evidence={"title": "llm", "season": "llm", "episode": "llm", "fansub": "llm"},
    )

    verdict = arbitrate(make_input(l1_result=l1, fused=fused, l3_result=l3, operation_id="op-1"))

    result = verdict.result
    assert result is not None
    assert result.season == 1
    assert result.evidence["season"] == "llm"
    assert result.fansub == "LoliHouse"
    assert result.level is Confidence.HIGH
    assert result.missing_fields == ()
    upgrade = detail_of(verdict, AUDIT_LEVEL_UPGRADED)
    assert upgrade["from"] == "medium"
    assert upgrade["to"] == "high"
    assert RULE_R5_VERIFIED in upgrade["rules"]
    assert upgrade["operation_id"] == "op-1"


def test_arbitrate_l3_conflict_keeps_winner_and_audits() -> None:
    fused = make_result(title="Sousou no Frieren", season=1)
    l3 = make_result(
        title="Wrong Title",
        season=2,
        evidence={"title": "llm", "season": "llm", "segment": "llm"},
    )

    verdict = arbitrate(make_input(l1_result=fused, fused=fused, l3_result=l3))

    result = verdict.result
    assert result is not None
    assert result.title == "Sousou no Frieren"
    assert result.season == 1
    assert result.evidence["season"] == "name"
    conflicts = audits_of(verdict, AUDIT_FIELD_CONFLICT)
    fields = {audit.detail["field"] for audit in conflicts}
    assert fields == {"title", "season"}
    title_conflict = next(a for a in conflicts if a.detail["field"] == "title")
    assert title_conflict.detail["fused_value"] == "Sousou no Frieren"
    assert title_conflict.detail["l3_value"] == "Wrong Title"
    assert title_conflict.detail["l3_evidence"] == "llm"
    assert audits_of(verdict, AUDIT_LEVEL_UPGRADED) == []


def test_arbitrate_r6_disambiguates_season_via_l3() -> None:
    fused = make_result(
        title="Frieren", season=1, evidence={"title": "name", "season": "memory", "segment": "name"}
    )
    l3 = make_result(title="Frieren", season=2, evidence={"title": "llm", "segment": "llm"})

    verdict = arbitrate(
        make_input(l1_result=fused, fused=fused, l3_result=l3, memory_seasons=(1, 2))
    )

    result = verdict.result
    assert result is not None
    assert result.season == 2
    assert result.evidence["season"] == "llm"
    detail = detail_of(verdict, AUDIT_SEASON_DISAMBIGUATED)
    assert detail["chosen"] == 2
    assert detail["source"] == "llm"
    assert detail["memory_seasons"] == "1,2"


def test_arbitrate_r6_disambiguates_season_via_reference() -> None:
    fused = make_result(
        title="Frieren", season=2, evidence={"title": "name", "season": "memory", "segment": "name"}
    )
    l3 = make_result(title="Frieren", season=2, evidence={"title": "llm", "segment": "llm"})
    reference = ReferenceFacts(canonical_title="Frieren", seasons=(1,))

    verdict = arbitrate(
        make_input(
            l1_result=fused,
            fused=fused,
            l3_result=l3,
            reference=reference,
            memory_seasons=(1, 2),
        )
    )

    result = verdict.result
    assert result is not None
    assert result.season == 1
    assert result.evidence["season"] == "reference"
    assert detail_of(verdict, AUDIT_SEASON_DISAMBIGUATED)["chosen"] == 1


def test_arbitrate_r6_rejects_out_of_set_season_and_audits() -> None:
    fused = make_result(
        title="Frieren", season=1, evidence={"title": "name", "season": "memory", "segment": "name"}
    )
    l3 = make_result(title="Frieren", season=7, evidence={"title": "llm", "segment": "llm"})

    verdict = arbitrate(
        make_input(l1_result=fused, fused=fused, l3_result=l3, memory_seasons=(1, 2))
    )

    result = verdict.result
    assert result is not None
    assert result.season == 1  # 不动
    detail = detail_of(verdict, AUDIT_SEASON_DISAMBIGUATION_REJECTED)
    assert detail["candidate"] == 7
    assert detail["source"] == "llm"


def test_arbitrate_reference_side_evidence_raises_l3_only_to_high() -> None:
    l3 = make_result(
        title="Sousou no Frieren",
        season=1,
        episode=None,
        evidence={"title": "llm", "season": "llm", "segment": "llm"},
    )
    reference = ReferenceFacts(canonical_title="sousou no frieren", seasons=(1,), source="bangumi")

    verdict = arbitrate(make_input(l3_result=l3, reference=reference))

    result = verdict.result
    assert result is not None
    assert result.level is Confidence.HIGH
    upgrade = detail_of(verdict, AUDIT_LEVEL_UPGRADED)
    assert RULE_R5_BASE_MEDIUM in upgrade["rules"]
    assert RULE_R5_VERIFIED in upgrade["rules"]
    assert upgrade["from"] == "medium"
    assert upgrade["to"] == "high"


def test_arbitrate_l3_only_without_reference_stays_medium() -> None:
    l3 = make_result(
        title="Unknown Show",
        season=1,
        episode=None,
        evidence={"title": "llm", "season": "llm", "segment": "llm"},
    )

    verdict = arbitrate(make_input(l3_result=l3))

    result = verdict.result
    assert result is not None
    assert result.level is Confidence.MEDIUM
    assert audits_of(verdict, AUDIT_LEVEL_UPGRADED) == []


def test_arbitrate_l3_only_builds_result_with_llm_evidence() -> None:
    l3 = make_result(
        title="Unknown Show",
        season=1,
        episode=2,
        fansub="SubGroup",
        evidence={
            "title": "llm",
            "season": "llm",
            "episode": "llm",
            "fansub": "llm",
            "segment": "llm",
        },
    )

    verdict = arbitrate(make_input(l3_result=l3))

    result = verdict.result
    assert result is not None
    assert result.title == "Unknown Show"
    assert result.season == 1
    assert result.episode == 2
    assert result.fansub == "SubGroup"
    assert result.evidence == {
        "title": "llm",
        "season": "llm",
        "episode": "llm",
        "fansub": "llm",
        "segment": "llm",
    }


def test_arbitrate_r4_consistency_upgrades_and_audits() -> None:
    l1 = make_result(title="Frieren", season=1, episode=3)
    fused = make_result(title="Frieren", season=1, episode=3)
    l3 = make_result(
        title="frieren", season=1, episode=3, evidence={"title": "llm", "segment": "llm"}
    )

    verdict = arbitrate(make_input(l1_result=l1, fused=fused, l3_result=l3))

    result = verdict.result
    assert result is not None
    assert result.level is Confidence.HIGH
    upgrade = detail_of(verdict, AUDIT_LEVEL_UPGRADED)
    assert upgrade["rules"] == RULE_R4_CONSISTENCY
    assert upgrade["from"] == "medium"
    assert upgrade["to"] == "high"


def test_arbitrate_without_operation_id_omits_batch_key() -> None:
    fused = make_result()

    verdict = arbitrate(make_input(fused=fused))

    assert verdict.audit
    assert all("operation_id" not in audit.detail for audit in verdict.audit)


def test_arbitrate_extra_base_evidence_keys_are_preserved() -> None:
    fused = make_result(
        title="Frieren",
        season=None,
        evidence={"title": "name", "segment": "name", "release_progress": "context"},
    )
    l3 = make_result(
        title="Frieren", season=1, evidence={"title": "llm", "season": "llm", "segment": "llm"}
    )

    verdict = arbitrate(make_input(fused=fused, l3_result=l3))

    result = verdict.result
    assert result is not None
    assert result.evidence["release_progress"] == "context"
    assert result.evidence["season"] == "llm"


def test_arbitrate_memory_season_ambiguity_without_l3_conclusion_stays() -> None:
    fused = make_result(
        title="Frieren", season=1, evidence={"title": "name", "season": "memory", "segment": "name"}
    )
    l3 = make_result(
        title="Frieren", season=None, evidence={"title": "llm", "segment": "llm"}
    )

    verdict = arbitrate(
        make_input(l1_result=fused, fused=fused, l3_result=l3, memory_seasons=(1, 2))
    )

    result = verdict.result
    assert result is not None
    assert result.season == 1
    assert verdict.audit == ()
