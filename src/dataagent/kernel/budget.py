"""三层预算（成本 / 次数 / 耗时）扣减与耗尽判定。

data-model.md Task.budget 结构；档位成本扣减规则（FR-008）：
后处理 0 次、参数重跑 1 次、换引擎 2 次。
耗尽触发升级（BudgetExceeded）——SC-013「无无限循环，预算注入全部按预期升级」。
"""

from dataclasses import dataclass
from enum import IntEnum


class Tier(IntEnum):
    """兜底档位（FR-008）：原样采用 / 后处理 / 参数重跑 / 换引擎。"""

    T0 = 0  # 原样采用
    T1 = 1  # 后处理
    T2 = 2  # 参数重跑
    T3 = 3  # 换引擎


# FR-008：按档扣成本（后处理 0 次、参数重跑 1 次、换引擎 2 次）
TIER_COST: dict[Tier, int] = {Tier.T0: 0, Tier.T1: 0, Tier.T2: 1, Tier.T3: 2}


class BudgetExceeded(RuntimeError):
    """预算耗尽，触发升级。"""


@dataclass(frozen=True)
class Budget:
    """任务预算上限（三层）。"""

    max_cost: int
    max_attempts: int
    max_duration_s: float


@dataclass(frozen=True)
class BudgetSpend:
    """已扣减的三层用量（不可变）。"""

    cost: int = 0
    attempts: int = 0
    duration_s: float = 0.0


class BudgetTracker:
    """三层预算扣减器：record 扣减，任一层耗尽即抛 BudgetExceeded。"""

    def __init__(self, budget: Budget) -> None:
        self._budget = budget
        self._spend = BudgetSpend()

    @property
    def budget(self) -> Budget:
        return self._budget

    @property
    def spend(self) -> BudgetSpend:
        return self._spend

    @property
    def exhausted(self) -> bool:
        """任一层用量已达上限即视为耗尽。"""
        return (
            self._spend.cost >= self._budget.max_cost
            or self._spend.attempts >= self._budget.max_attempts
            or self._spend.duration_s >= self._budget.max_duration_s
        )

    def record(self, tier: Tier, duration_s: float = 0.0) -> BudgetSpend:
        """记录一次动作：按档扣成本、次数 +1、耗时累计；耗尽触发升级。"""
        self._spend = BudgetSpend(
            cost=self._spend.cost + TIER_COST[tier],
            attempts=self._spend.attempts + 1,
            duration_s=self._spend.duration_s + duration_s,
        )
        if self.exhausted:
            raise BudgetExceeded(
                f"预算耗尽（cost={self._spend.cost}/{self._budget.max_cost}, "
                f"attempts={self._spend.attempts}/{self._budget.max_attempts}, "
                f"duration={self._spend.duration_s:g}/{self._budget.max_duration_s:g}s），触发升级"
            )
        return self._spend
