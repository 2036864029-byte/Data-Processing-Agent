"""B-PLAN 任务级执行规划测试（T120 / FR-026 / PRD 4.1.4 规则 7–9）。

- 仅允许 3 种 stage kind（processing / decision / replan），禁止自由图结构；
- 分支表达式复用受限表达式引擎，白名单仅限统计指标，未知指标生成期拒绝；
- 三条 guards 为硬上限，超出即停止交付带缺口结果；
- plan 一经冻结不可改，同版本重放逐字一致；重规划产生新 plan_version 可 diff。
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from dataagent.kernel.plan import (
    DEFAULT_MAX_MODEL_CALLS,
    DEFAULT_MAX_REPLANS,
    DEFAULT_MAX_WALL_TIME_S,
    PLAN_METRICS,
    FrozenPlan,
    PlanBranchError,
    PlanExecutor,
    PlanGuardExceeded,
    PlanGuardTracker,
    PlanMetricMissingError,
    PlanValidationError,
    parse_plan,
)


def plan_dict() -> dict[str, object]:
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
                    {"when": {"<": [{"var": "stp_rate"}, 0.75]}, "goto": "bulk_quality"},
                    {"else": True, "goto": "bulk_balanced"},
                ],
            },
            {
                "id": "bulk_fast",
                "kind": "processing",
                "scope": {"pages": "101-"},
                "strategy": "fast",
            },
            {
                "id": "bulk_quality",
                "kind": "processing",
                "scope": {"pages": "101-"},
                "strategy": "quality",
            },
            {
                "id": "bulk_balanced",
                "kind": "processing",
                "scope": {"pages": "101-"},
                "strategy": "balanced",
            },
        ],
        "guards": {
            "max_model_calls": 8,
            "max_replans": 3,
            "max_wall_time_s": 3600,
        },
    }


def replan_plan_dict(*, max_replans: int = 3) -> dict[str, object]:
    return {
        "version": 1,
        "stages": [
            {"id": "probe", "kind": "processing", "scope": {"pages": "1-100"}},
            {
                "id": "maybe_replan",
                "kind": "replan",
                "on": {"metric": "stp_rate", "window": "probe"},
                "branches": [{"else": True, "goto": "bulk"}],
            },
            {
                "id": "bulk",
                "kind": "processing",
                "scope": {"pages": "101-"},
                "strategy": "balanced",
            },
        ],
        "guards": {"max_replans": max_replans},
    }


class TestParseAndValidate:
    def test_parses_three_stage_kinds(self) -> None:
        plan = parse_plan(plan_dict())
        assert [stage.kind.value for stage in plan.spec.stages] == [
            "processing",
            "decision",
            "processing",
            "processing",
            "processing",
        ]

    def test_unknown_stage_kind_rejected(self) -> None:
        data = plan_dict()
        data["stages"][0]["kind"] = "loop"  # type: ignore[index]
        with pytest.raises(PlanValidationError, match="kind"):
            parse_plan(data)

    def test_free_graph_rejected(self) -> None:
        data = plan_dict()
        data["stages"][1]["branches"][0]["goto"] = "missing"  # type: ignore[index]
        with pytest.raises(PlanValidationError, match="missing"):
            parse_plan(data)

    def test_backward_jump_rejected(self) -> None:
        data = plan_dict()
        data["stages"][1]["branches"][0]["goto"] = "probe"  # type: ignore[index]
        with pytest.raises(PlanValidationError, match="向前"):
            parse_plan(data)

    def test_duplicate_stage_id_rejected(self) -> None:
        data = plan_dict()
        data["stages"][2]["id"] = "probe"  # type: ignore[index]
        with pytest.raises(PlanValidationError, match="重复"):
            parse_plan(data)

    def test_unknown_metric_rejected_at_compile(self) -> None:
        data = plan_dict()
        data["stages"][1]["branches"][0]["when"] = {  # type: ignore[index]
            ">=": [{"var": "not_a_metric"}, 0.85]
        }
        with pytest.raises(PlanValidationError, match="白名单|未声明|not_a_metric"):
            parse_plan(data)

    def test_non_statistical_field_rejected(self) -> None:
        data = plan_dict()
        data["stages"][1]["branches"][0]["when"] = {  # type: ignore[index]
            "==": [{"var": "class.layout.columns"}, 2]
        }
        with pytest.raises(PlanValidationError):
            parse_plan(data)

    def test_decision_metric_must_be_whitelisted(self) -> None:
        data = plan_dict()
        data["stages"][1]["on"] = {"metric": "extract_quality"}  # type: ignore[index]
        with pytest.raises(PlanValidationError, match="统计指标"):
            parse_plan(data)

    def test_string_expression_rejected_until_p1(self) -> None:
        data = plan_dict()
        data["stages"][1]["branches"][0]["when"] = "stp_rate >= 0.85"  # type: ignore[index]
        with pytest.raises(PlanValidationError, match="P1"):
            parse_plan(data)

    def test_guards_defaults(self) -> None:
        data = plan_dict()
        del data["guards"]
        plan = parse_plan(data)
        assert plan.guards.max_model_calls == DEFAULT_MAX_MODEL_CALLS == 8
        assert plan.guards.max_replans == DEFAULT_MAX_REPLANS == 3
        assert plan.guards.max_wall_time_s == DEFAULT_MAX_WALL_TIME_S == 3600

    @pytest.mark.parametrize("field", ["max_model_calls", "max_replans", "max_wall_time_s"])
    def test_guards_must_be_positive(self, field: str) -> None:
        data = plan_dict()
        data["guards"][field] = 0  # type: ignore[index]
        with pytest.raises(PlanValidationError):
            parse_plan(data)

    def test_invalid_scope_rejected(self) -> None:
        data = plan_dict()
        data["stages"][0]["scope"] = {"pages": "100-1"}  # type: ignore[index]
        with pytest.raises(PlanValidationError, match="pages|范围"):
            parse_plan(data)

    def test_else_branch_must_be_last(self) -> None:
        data = plan_dict()
        branches = data["stages"][1]["branches"]  # type: ignore[index]
        branches.insert(0, {"else": True, "goto": "bulk_balanced"})
        with pytest.raises(PlanValidationError, match="else"):
            parse_plan(data)

    def test_branch_requires_exactly_one_condition(self) -> None:
        data = plan_dict()
        data["stages"][1]["branches"][0]["else"] = True  # type: ignore[index]
        with pytest.raises(PlanValidationError, match="when|else"):
            parse_plan(data)

    def test_decision_requires_branches(self) -> None:
        data = plan_dict()
        data["stages"][1]["branches"] = []  # type: ignore[index]
        with pytest.raises(PlanValidationError, match="branches|分支"):
            parse_plan(data)


class TestFreezeAndReplay:
    def test_frozen_plan_is_immutable(self) -> None:
        plan = parse_plan(plan_dict())
        with pytest.raises(ValidationError):
            plan.spec.stages[0].id = "changed"  # type: ignore[index,misc]

    def test_same_plan_parses_to_identical_fingerprint(self) -> None:
        first = parse_plan(plan_dict())
        second = parse_plan(copy.deepcopy(plan_dict()))
        assert first.canonical_json == second.canonical_json
        assert first.fingerprint == second.fingerprint

    def test_replay_yields_identical_decisions(self) -> None:
        plan = parse_plan(plan_dict())
        first = PlanExecutor(plan).resolve({"stp_rate": 0.9})
        second = PlanExecutor(plan).resolve({"stp_rate": 0.9})
        assert first.decisions == second.decisions
        assert [stage.id for stage in first.stages] == [
            stage.id for stage in second.stages
        ]

    def test_contract_plan_matches_spec(self) -> None:
        plan = parse_plan(plan_dict())
        contract = plan.contract_plan()
        assert contract.plan_version == 1
        assert [stage.id for stage in contract.stages] == [
            stage.id for stage in plan.spec.stages
        ]


class TestBranchEvaluation:
    def test_high_stp_takes_fast_branch(self) -> None:
        plan = parse_plan(plan_dict())
        resolution = PlanExecutor(plan).resolve({"stp_rate": 0.9})
        assert [stage.id for stage in resolution.stages] == ["probe", "bulk_fast"]
        assert resolution.decisions[0].branch_taken == "bulk_fast"

    def test_low_stp_takes_quality_branch(self) -> None:
        plan = parse_plan(plan_dict())
        resolution = PlanExecutor(plan).resolve({"stp_rate": 0.7})
        assert [stage.id for stage in resolution.stages] == ["probe", "bulk_quality"]

    def test_middle_stp_takes_else_branch(self) -> None:
        plan = parse_plan(plan_dict())
        resolution = PlanExecutor(plan).resolve({"stp_rate": 0.8})
        assert [stage.id for stage in resolution.stages] == ["probe", "bulk_balanced"]
        assert resolution.decisions[0].branch_taken == "bulk_balanced"

    def test_decision_snapshot_is_recorded(self) -> None:
        plan = parse_plan(plan_dict())
        resolution = PlanExecutor(plan).resolve({"stp_rate": 0.9, "pages_total": 100})
        snapshot = resolution.decisions[0].metric_snapshot
        assert snapshot["stp_rate"] == 0.9
        assert snapshot["pages_total"] == 100
        assert set(snapshot) <= PLAN_METRICS

    def test_missing_runtime_metric_rejected(self) -> None:
        plan = parse_plan(plan_dict())
        with pytest.raises(PlanMetricMissingError, match="stp_rate"):
            PlanExecutor(plan).resolve({})

    def test_branch_without_match_raises(self) -> None:
        data = plan_dict()
        data["stages"][1]["branches"] = data["stages"][1]["branches"][:2]  # type: ignore[index]
        plan = parse_plan(data)
        with pytest.raises(PlanBranchError):
            PlanExecutor(plan).resolve({"stp_rate": 0.8})


class TestGuards:
    def test_replan_guard_stops_after_limit(self) -> None:
        plan = parse_plan(replan_plan_dict(max_replans=1))
        tracker = PlanGuardTracker(plan.guards)
        PlanExecutor(plan, tracker=tracker).resolve({"stp_rate": 0.9})
        with pytest.raises(PlanGuardExceeded) as excinfo:
            PlanExecutor(plan, tracker=tracker).resolve({"stp_rate": 0.9})
        assert excinfo.value.guard == "max_replans"
        assert [stage.id for stage in excinfo.value.partial.stages] == ["probe"]

    def test_model_call_guard_stops_plan(self) -> None:
        plan = parse_plan(plan_dict())
        tracker = PlanGuardTracker(plan.guards)
        tracker.record_model_call(9)
        with pytest.raises(PlanGuardExceeded) as excinfo:
            PlanExecutor(plan, tracker=tracker).resolve({"stp_rate": 0.9})
        assert excinfo.value.guard == "max_model_calls"
        assert excinfo.value.partial.stages == ()

    def test_wall_time_guard_stops_plan(self) -> None:
        plan = parse_plan(plan_dict())
        clock = iter([0.0, 0.0, 4000.0])
        tracker = PlanGuardTracker(plan.guards, clock=lambda: next(clock))
        with pytest.raises(PlanGuardExceeded) as excinfo:
            PlanExecutor(plan, tracker=tracker).resolve({"stp_rate": 0.9})
        assert excinfo.value.guard == "max_wall_time_s"


class TestReplanVersioning:
    def test_replan_creates_new_version_with_diff(self) -> None:
        plan = parse_plan(plan_dict())
        changed = plan_dict()
        changed["stages"][2]["strategy"] = "balanced"  # type: ignore[index]
        next_plan = plan.replan_with(changed["stages"])

        assert isinstance(next_plan, FrozenPlan)
        assert next_plan.version == plan.version + 1
        assert next_plan.fingerprint != plan.fingerprint
        assert next_plan.diff(plan) == ("bulk_fast",)

    def test_replan_preserves_guards(self) -> None:
        plan = parse_plan(plan_dict())
        next_plan = plan.replan_with(plan_dict()["stages"])
        assert next_plan.guards == plan.guards
        assert next_plan.diff(plan) == ()
