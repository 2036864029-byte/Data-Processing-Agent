"""任务级 3 个决策点规则阈值测试（T053，A8）。

Replan 阈值触发（如 40% 页失败 → 整本转 OCR）；Strategy 整本策略切换；
P0 用规则阈值，M3 才可加模型实现。
"""

from dataclasses import asdict

import pytest

from dataagent.contracts.policies import Plan, StageKind, Strategy
from dataagent.kernel.policies.model_plan import ModelPlanPolicy
from dataagent.kernel.policies.model_replan import ModelReplanPolicy
from dataagent.kernel.policies.model_strategy import ModelStrategyPolicy
from dataagent.kernel.policies.rule_replan import RuleReplanPolicy
from dataagent.kernel.policies.rule_strategy import RuleStrategyPolicy


def test_replan_triggers_whole_book_ocr_at_forty_percent() -> None:
    policy = RuleReplanPolicy()
    plan = policy.decide(
        {"failed_page_ratio": 0.40, "plan_version": 1, "replans_used": 0}
    )

    assert isinstance(plan, Plan)
    assert plan.plan_version == 2
    assert len(plan.stages) == 1
    stage = plan.stages[0]
    assert stage.kind is StageKind.PROCESSING
    assert stage.scope == "1-"
    assert stage.strategy is Strategy.WHOLE_BOOK_OCR
    assert plan.created_by == "rule_template"


def test_replan_does_not_trigger_below_threshold() -> None:
    assert RuleReplanPolicy().decide({"failed_page_ratio": 0.39}) is None
    assert RuleReplanPolicy().decide({}) is None


def test_replan_respects_max_replans_guard() -> None:
    """重规划次数超上限即停止（不允许无限重规划）。"""
    policy = RuleReplanPolicy()
    stats = {
        "failed_page_ratio": 0.60,
        "replans_used": 3,
        "max_replans": 3,
    }
    assert policy.decide(stats) is None


def test_replan_is_deterministic_and_serializable() -> None:
    policy = RuleReplanPolicy()
    stats = {"failed_page_ratio": 0.55, "plan_version": 4}

    first = policy.decide(stats)
    second = policy.decide(stats)
    assert first == second
    assert first is not None
    assert asdict(first)["plan_version"] == 5


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        ({"budget_exhausted": True}, Strategy.ABORT),
        ({"class_confidence": 0.80}, Strategy.WHOLE_BOOK_OCR),
        ({"failed_page_ratio": 0.45}, Strategy.WHOLE_BOOK_OCR),
        ({"stp_rate": 0.70, "stp_target": 0.85}, Strategy.DOWNGRADE),
        ({}, Strategy.DOWNGRADE),
    ],
)
def test_strategy_policy_thresholds(stats: dict[str, object], expected: Strategy) -> None:
    policy = RuleStrategyPolicy()
    assert policy.select(stats) is expected
    assert policy.select(stats) is expected


def test_abort_takes_priority_over_other_signals() -> None:
    policy = RuleStrategyPolicy()
    stats = {
        "budget_exhausted": True,
        "class_confidence": 0.50,
        "failed_page_ratio": 0.90,
    }
    assert policy.select(stats) is Strategy.ABORT


def test_model_implementations_are_p0_placeholders() -> None:
    """P0 只使用规则实现；模型实现留到 M3，且不引入 pydantic-ai。"""
    with pytest.raises(NotImplementedError):
        ModelPlanPolicy().compile("把年报转成 JSON", {})
    with pytest.raises(NotImplementedError):
        ModelReplanPolicy().decide({})
    with pytest.raises(NotImplementedError):
        ModelStrategyPolicy().select({})
