"""NL → Plan 模型策略测试：严格 schema、重试、预算与冻结。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from dataagent.contracts.policies import Plan as ContractPlan
from dataagent.kernel.plan import PlanGuardExceeded, PlanGuards, PlanGuardTracker
from dataagent.kernel.policies.model_plan import (
    ModelPlanPolicy,
    PlanCompileError,
    build_plan_instruction,
)


def plan_draft() -> dict[str, Any]:
    return {
        "version": 1,
        "stages": [
            {
                "id": "probe",
                "kind": "processing",
                "scope": {"pages": "1-100"},
                "strategy": "quality",
            },
            {
                "id": "decide",
                "kind": "decision",
                "on": {"metric": "stp_rate", "window": "probe"},
                "branches": [
                    {"when": {">=": [{"var": "stp_rate"}, 0.85]}, "goto": "bulk_fast"},
                    {"else": True, "goto": "bulk_safe"},
                ],
            },
            {
                "id": "bulk_fast",
                "kind": "processing",
                "scope": {"pages": "101-"},
                "strategy": "fast",
            },
            {
                "id": "bulk_safe",
                "kind": "processing",
                "scope": {"pages": "101-"},
                "strategy": "quality",
            },
        ],
        "guards": {
            "max_model_calls": 8,
            "max_replans": 3,
            "max_wall_time_s": 3600,
        },
        "created_by": "rule_template",
    }


class StubDraftModel:
    def __init__(self, drafts: list[Mapping[str, Any]]) -> None:
        self._drafts = drafts
        self.calls: list[dict[str, Any]] = []

    def draft(
        self,
        *,
        instruction: str,
        nl: str,
        task_stats: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.calls.append(
            {"instruction": instruction, "nl": nl, "stats": dict(task_stats)}
        )
        if len(self._drafts) > 1:
            return self._drafts.pop(0)
        return self._drafts[0]


def test_compile_freezes_model_plan() -> None:
    model = StubDraftModel([plan_draft()])
    policy = ModelPlanPolicy(model)

    frozen = policy.compile_frozen("先探路再决定", {"stp_rate": 0.9})

    assert frozen.version == 1
    assert frozen.spec.created_by == "model"
    assert [stage.id for stage in frozen.stages] == [
        "probe",
        "decide",
        "bulk_fast",
        "bulk_safe",
    ]
    assert len(model.calls) == 1


def test_compile_protocol_returns_contract_plan() -> None:
    policy = ModelPlanPolicy(StubDraftModel([plan_draft()]))

    plan = policy.compile("先探路", {})

    assert isinstance(plan, ContractPlan)
    assert plan.plan_version == 1


def test_invalid_draft_is_retried_with_validation_feedback() -> None:
    bad = plan_draft()
    bad["stages"][1]["branches"][0]["when"] = {  # type: ignore[index]
        ">=": [{"var": "unknown_metric"}, 0.85]
    }
    model = StubDraftModel([bad, plan_draft()])
    policy = ModelPlanPolicy(model, max_attempts=2)

    frozen = policy.compile_frozen("先探路", {})

    assert frozen.version == 1
    assert len(model.calls) == 2


def test_invalid_draft_exhausts_attempts() -> None:
    bad = plan_draft()
    bad["stages"][0]["kind"] = "loop"  # type: ignore[index]
    model = StubDraftModel([bad])
    policy = ModelPlanPolicy(model, max_attempts=2)

    with pytest.raises(PlanCompileError):
        policy.compile_frozen("先探路", {})
    assert len(model.calls) == 2


def test_model_call_budget_stops_compile() -> None:
    bad = plan_draft()
    bad["stages"][0]["kind"] = "loop"  # type: ignore[index]
    tracker = PlanGuardTracker(PlanGuards(max_model_calls=1))
    policy = ModelPlanPolicy(
        StubDraftModel([bad]), max_attempts=2, budget=tracker
    )

    with pytest.raises(PlanGuardExceeded) as excinfo:
        policy.compile_frozen("先探路", {})

    assert excinfo.value.guard == "max_model_calls"
    assert tracker.model_calls == 1


def test_instruction_lists_metrics_and_stats() -> None:
    instruction = build_plan_instruction({"stp_rate": 0.9, "pages_total": 100})

    assert "stp_rate" in instruction
    assert "pages_total" in instruction
    assert '"stp_rate": 0.9' in instruction


def test_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ModelPlanPolicy(StubDraftModel([plan_draft()]), max_attempts=0)
