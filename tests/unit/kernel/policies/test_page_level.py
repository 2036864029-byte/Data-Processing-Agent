"""页级 5 个规则实现确定性测试（T052，A8）。

Route / Tier / Operator / Attempt / Escalation：同一输入两次调用结果一致；
输入输出可序列化并写 Trace。
"""

import json

import pytest

from dataagent.contracts.defects import Defect, OperatorResult, RepairOperator
from dataagent.contracts.errors import DefectCode
from dataagent.contracts.policies import (
    Attribution,
    AttributionLabel,
    ChainId,
    PageProfile,
    Tier,
)
from dataagent.kernel.policies.rule_attempt import RuleAttemptPolicy, has_progress
from dataagent.kernel.policies.rule_escalation import RuleEscalationPolicy
from dataagent.kernel.policies.rule_operator import (
    NoRepairOperatorError,
    RuleOperatorPolicy,
)
from dataagent.kernel.policies.rule_route import RuleRoutePolicy
from dataagent.kernel.policies.rule_tier import RuleTierPolicy
from dataagent.pipeline.repair.mapping import DEFECT_OPERATOR_MAPPING


def make_profile(
    *,
    pdf_type: str = "text_based",
    route: str = "native",
    class_confidence: float = 0.95,
    columns: int = 1,
    has_table: bool = False,
    is_complex: bool = False,
    rotation: int = 0,
    glyph_coverage: float = 0.99,
    header_footer_ratio: float = 0.0,
) -> PageProfile:
    return PageProfile.model_validate(
        {
            "doc_id": "sha256:doc",
            "page_no": 1,
            "class": {
                "pdf_type": pdf_type,
                "class_confidence": class_confidence,
                "route": route,
                "route_reason": [],
            },
            "layout": {
                "columns": columns,
                "has_table": has_table,
                "is_complex": is_complex,
                "rotation": rotation,
            },
            "signals": {
                "char_density": 0.01,
                "glyph_coverage": glyph_coverage,
                "empty_region_ratio": 0.0,
                "header_footer_ratio": header_footer_ratio,
            },
        }
    )


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("native", ChainId.PDF_NATIVE),
        ("ocr", ChainId.PDF_OCR),
        ("vlm", ChainId.PDF_VLM),
    ],
)
def test_route_policy_is_deterministic(route: str, expected: ChainId) -> None:
    policy = RuleRoutePolicy()
    profile = make_profile(route=route)

    assert policy.route(profile) is expected
    assert policy.route(profile) is expected


@pytest.mark.parametrize(
    ("overrides", "quality", "expected"),
    [
        ({}, 0.90, Tier.T0),
        ({"has_table": True}, 0.90, Tier.T1),
        ({"header_footer_ratio": 0.10}, 0.90, Tier.T1),
        ({"columns": 2}, 0.90, Tier.T2),
        ({"is_complex": True}, 0.90, Tier.T2),
        ({"rotation": 90}, 0.90, Tier.T2),
        ({}, 0.70, Tier.T2),
        ({"route": "ocr"}, 0.90, Tier.T3),
        ({"pdf_type": "scanned"}, 0.90, Tier.T3),
        ({"class_confidence": 0.80}, 0.90, Tier.T3),
        ({"glyph_coverage": 0.90}, 0.90, Tier.T3),
        ({"route": "ocr", "has_table": False}, 0.90, Tier.T3),
    ],
)
def test_tier_policy_follows_cost_ladder(
    overrides: dict[str, object], quality: float, expected: Tier
) -> None:
    policy = RuleTierPolicy()
    profile = make_profile(**overrides)  # type: ignore[arg-type]

    first = policy.tier(profile, quality)
    second = policy.tier(profile, quality)
    assert first is expected
    assert second is expected


def make_defect(code: DefectCode, **overrides: object) -> Defect:
    data: dict[str, object] = {
        "code": code,
        "location": {"page_no": 1},
        "suggested_action": RepairOperator.OCR_PAGE,
    }
    data.update(overrides)
    return Defect.model_validate(data)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (DefectCode.ENCODING_LOSS, RepairOperator.OCR_PAGE),
        (DefectCode.LOW_DENSITY, RepairOperator.OCR_PAGE),
        (DefectCode.REGION_EMPTY, RepairOperator.EXTRACT_REGION),
        (DefectCode.TABLE_STRUCTURE_BROKEN, RepairOperator.REBUILD_TABLE_CELLS),
        (DefectCode.TABLE_CROSS_PAGE_BREAK, RepairOperator.MERGE_CROSS_PAGE_TABLE),
        (DefectCode.READING_ORDER_BROKEN, RepairOperator.REORDER_BLOCKS),
        (DefectCode.COLUMN_MIXED, RepairOperator.RESEGMENT_COLUMNS),
        (DefectCode.HEADER_FOOTER_LEAK, RepairOperator.STRIP_HEADER_FOOTER),
        (DefectCode.PAGE_ROTATED, RepairOperator.ROTATE_PAGE),
    ],
)
def test_operator_policy_reads_fixed_mapping(
    code: DefectCode, expected: RepairOperator
) -> None:
    """算子来自写死映射表（suggested_action 仅供报告，不参与推断）。"""
    policy = RuleOperatorPolicy()
    first = policy.operator(make_defect(code))
    second = policy.operator(make_defect(code))
    assert first is expected
    assert second is expected
    assert DEFECT_OPERATOR_MAPPING[code] is expected


def test_operator_policy_rejects_codes_without_operator() -> None:
    policy = RuleOperatorPolicy()
    with pytest.raises(NoRepairOperatorError):
        policy.operator(make_defect(DefectCode.PAGE_NUMBER_BROKEN, suggested_action=None))
    with pytest.raises(NoRepairOperatorError):
        policy.operator(
            make_defect(
                DefectCode.PLUGIN_REASON_UNKNOWN,
                raw_reason="unknown_reason_xyz",
                suggested_action=None,
            )
        )


def test_attempt_policy_stops_on_no_change() -> None:
    """三层停机最终门控：changed == false 立即停机，不重试同参数。"""
    policy = RuleAttemptPolicy()
    changed = OperatorResult(doc_id="sha256:doc", page_no=1, changed=True)
    unchanged = OperatorResult(doc_id="sha256:doc", page_no=1, changed=False)

    assert policy.continue_(changed) is True
    assert policy.continue_(unchanged) is False
    assert policy.continue_(changed) is True


def test_attempt_policy_no_progress_threshold_is_five_percent() -> None:
    """无进展判定：指标提升 < 5% 判该算子无效。"""
    assert has_progress(0.50, 0.54) is False
    assert has_progress(0.50, 0.55) is True
    assert has_progress(0.50, 0.49) is False
    assert has_progress(0.50, 0.54, min_gain=0.04) is True


class FakeBudget:
    def __init__(self, exhausted: bool) -> None:
        self._exhausted = exhausted

    @property
    def exhausted(self) -> bool:
        return self._exhausted


def test_escalation_policy_on_budget_or_unattributable() -> None:
    policy = RuleEscalationPolicy()
    confident = Attribution(label=AttributionLabel.TABLE_BREAK, confidence=0.9)
    unsure = Attribution(label=AttributionLabel.ENCODING_BROKEN, confidence=0.3)

    assert policy.escalate(FakeBudget(exhausted=True), confident) is True
    assert policy.escalate(FakeBudget(exhausted=False), unsure) is True
    assert policy.escalate(FakeBudget(exhausted=False), confident) is False
    assert policy.escalate(FakeBudget(exhausted=False), confident) is False


def test_page_level_decisions_are_traceable() -> None:
    """输入 / 输出可序列化并写 Trace。"""
    profile = make_profile(has_table=True)
    decision = {
        "input": json.loads(profile.model_dump_json()),
        "route": RuleRoutePolicy().route(profile).value,
        "tier": RuleTierPolicy().tier(profile, 0.9).value,
    }
    assert json.dumps(decision)
    assert decision["route"] == "pdf.native"
    assert decision["tier"] == "T1"
