"""三层预算测试（T010，A2）。

成本 / 次数 / 耗时三层扣减；耗尽触发升级（data-model.md Task.budget）。
档位成本扣减规则（FR-008）：后处理 0 次、参数重跑 1 次、换引擎 2 次。
"""

import pytest

from dataagent.kernel.budget import TIER_COST, Budget, BudgetExceeded, BudgetTracker, Tier


def test_postprocess_deducts_zero_cost() -> None:
    """后处理（T1）扣 0 次成本，但计入次数与耗时。"""
    tracker = BudgetTracker(Budget(max_cost=10, max_attempts=10, max_duration_s=60.0))
    tracker.record(Tier.T1, duration_s=2.0)
    assert tracker.spend.cost == 0
    assert tracker.spend.attempts == 1
    assert tracker.spend.duration_s == 2.0


def test_tier_cost_weights_fr008() -> None:
    """FR-008：原样采用 0、后处理 0、参数重跑 1、换引擎 2。"""
    assert TIER_COST[Tier.T0] == 0
    assert TIER_COST[Tier.T1] == 0
    assert TIER_COST[Tier.T2] == 1
    assert TIER_COST[Tier.T3] == 2


def test_cost_exhaustion_escalates() -> None:
    """成本层耗尽触发升级。"""
    tracker = BudgetTracker(Budget(max_cost=3, max_attempts=100, max_duration_s=60.0))
    tracker.record(Tier.T3)  # cost=2
    assert not tracker.exhausted
    with pytest.raises(BudgetExceeded):
        tracker.record(Tier.T3)  # cost=4 ≥ 3 → 升级
    assert tracker.exhausted


def test_attempts_exhaustion() -> None:
    """次数层耗尽触发升级（T1/T0 成本为 0 也受次数约束，杜绝无限循环）。"""
    tracker = BudgetTracker(Budget(max_cost=100, max_attempts=2, max_duration_s=60.0))
    tracker.record(Tier.T0)
    with pytest.raises(BudgetExceeded):
        tracker.record(Tier.T0)


def test_duration_exhaustion() -> None:
    """耗时层耗尽触发升级。"""
    tracker = BudgetTracker(Budget(max_cost=100, max_attempts=100, max_duration_s=10.0))
    with pytest.raises(BudgetExceeded):
        tracker.record(Tier.T1, duration_s=10.5)


def test_tier_cost_stacks() -> None:
    """多档动作成本累计：T3 + T2 = 3。"""
    tracker = BudgetTracker(Budget(max_cost=10, max_attempts=10, max_duration_s=60.0))
    tracker.record(Tier.T3)
    tracker.record(Tier.T2)
    assert tracker.spend.cost == 3
    assert tracker.spend.attempts == 2
