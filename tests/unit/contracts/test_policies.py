"""8 个决策点 Policy Protocol 测试（T051，A8）。

逐字引用 contracts/policies.md 签名；Policy 无状态、可注入假实现、输入输出可序列化。
"""

import inspect
import json
from dataclasses import asdict

import pytest

from dataagent.contracts.defects import OperatorResult, RepairOperator
from dataagent.contracts.policies import (
    AttemptPolicy,
    Attribution,
    AttributionLabel,
    Budget,
    ChainId,
    EscalationPolicy,
    OperatorPolicy,
    PageProfile,
    Plan,
    PlanPolicy,
    PlanStage,
    ReplanPolicy,
    RoutePolicy,
    StageKind,
    Strategy,
    StrategyPolicy,
    Tier,
    TierPolicy,
)

PLAN = Plan(
    plan_version=1,
    stages=(PlanStage(id="probe", kind=StageKind.PROCESSING, scope="1-100"),),
)

PROFILE = PageProfile.model_validate(
    {
        "doc_id": "sha256:doc",
        "page_no": 1,
        "class": {
            "pdf_type": "text_based",
            "class_confidence": 0.95,
            "route": "native",
            "route_reason": [],
        },
    }
)
ATTRIBUTION = Attribution(label=AttributionLabel.TABLE_BREAK, confidence=0.9)


class FakePlanPolicy:
    def compile(self, nl: str, task_stats: dict) -> Plan:
        return PLAN


class FakeReplanPolicy:
    def decide(self, exec_stats: dict) -> Plan | None:
        return None


class FakeStrategyPolicy:
    def select(self, task_stats: dict) -> Strategy:
        return Strategy.DOWNGRADE


class FakeRoutePolicy:
    def route(self, profile: PageProfile) -> ChainId:
        return ChainId.PDF_NATIVE


class FakeTierPolicy:
    def tier(self, profile: PageProfile, quality: float) -> Tier:
        return Tier.T0


class FakeOperatorPolicy:
    def operator(self, defect: object) -> RepairOperator:
        return RepairOperator.OCR_PAGE


class FakeAttemptPolicy:
    def continue_(self, result: OperatorResult) -> bool:
        return result.changed


class FakeEscalationPolicy:
    def escalate(self, budget: Budget, attribution: Attribution) -> bool:
        return budget.exhausted


class FakeBudget:
    def __init__(self, exhausted: bool) -> None:
        self._exhausted = exhausted

    @property
    def exhausted(self) -> bool:
        return self._exhausted


def test_eight_protocols_accept_injectable_fakes() -> None:
    """假实现可注入：8 个 Protocol 均可由规则版 / 假实现结构化满足。"""
    assert isinstance(FakePlanPolicy(), PlanPolicy)
    assert isinstance(FakeReplanPolicy(), ReplanPolicy)
    assert isinstance(FakeStrategyPolicy(), StrategyPolicy)
    assert isinstance(FakeRoutePolicy(), RoutePolicy)
    assert isinstance(FakeTierPolicy(), TierPolicy)
    assert isinstance(FakeOperatorPolicy(), OperatorPolicy)
    assert isinstance(FakeAttemptPolicy(), AttemptPolicy)
    assert isinstance(FakeEscalationPolicy(), EscalationPolicy)
    assert isinstance(FakeBudget(exhausted=False), Budget)


def test_protocol_method_signatures_match_contract() -> None:
    """签名逐字引用 contracts/policies.md。"""
    expected = {
        PlanPolicy: ("compile", ["self", "nl", "task_stats"]),
        ReplanPolicy: ("decide", ["self", "exec_stats"]),
        StrategyPolicy: ("select", ["self", "task_stats"]),
        RoutePolicy: ("route", ["self", "profile"]),
        TierPolicy: ("tier", ["self", "profile", "quality"]),
        OperatorPolicy: ("operator", ["self", "defect"]),
        AttemptPolicy: ("continue_", ["self", "result"]),
        EscalationPolicy: ("escalate", ["self", "budget", "attribution"]),
    }
    for protocol, (method_name, parameters) in expected.items():
        parameters_actual = list(
            inspect.signature(getattr(protocol, method_name)).parameters
        )
        assert parameters_actual == parameters


def test_enums_are_closed_and_serializable() -> None:
    assert [tier.value for tier in Tier] == ["T0", "T1", "T2", "T3"]
    assert {strategy.value for strategy in Strategy} == {
        "whole_book_ocr",
        "downgrade",
        "abort",
    }
    assert ChainId.PDF_NATIVE.value == "pdf.native"
    assert ChainId.PDF_OCR.value == "pdf.ocr"
    assert json.dumps(ChainId.PDF_VLM.value) == '"pdf.vlm"'


def test_policy_inputs_and_outputs_are_serializable() -> None:
    """输入 / 输出可序列化并写 Trace（页级决策参与审计）。"""
    assert json.loads(PROFILE.model_dump_json())["page_no"] == 1
    json.dumps(asdict(PLAN))
    json.dumps(asdict(ATTRIBUTION))
    json.dumps({"chain": ChainId.PDF_NATIVE.value, "tier": Tier.T0.value})


def test_page_profile_two_phase_defaults() -> None:
    """路由阶段只有 class / layout；signals / quality 抽取后才有。"""
    assert PROFILE.signals is None
    assert PROFILE.quality is None
    assert PROFILE.columns == 1
    assert PROFILE.has_table is False


def test_page_profile_rejects_zero_page() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PageProfile.model_validate(
            {
                "doc_id": "sha256:doc",
                "page_no": 0,
                "class": {
                    "pdf_type": "text_based",
                    "class_confidence": 0.9,
                    "route": "native",
                },
            }
        )
