"""AttemptPolicy 规则实现：是否续试（三层停机的最终门控）。

三层停机（PRD 4.5.9）：
1. 次数 / 时间 / 成本：由 `kernel/budget.py` 的 BudgetTracker 在动作前保证——
   耗尽即抛 BudgetExceeded，不会再进入本策略。
2. 无进展：指标提升 < 5% 或缺陷数未减少 → 该算子判无效，由 `has_progress()` 判定。
3. `OperatorResult.changed == false`：本策略的最终门控，立即停机、不重试同参数。
同码唯一（同一缺陷码同一页只修一次）由增强回路在派发前保证。
规则本身无状态，可重放。
"""

from __future__ import annotations

from dataagent.contracts.defects import OperatorResult

MIN_PROGRESS: float = 0.05


class RuleAttemptPolicy:
    def continue_(self, result: OperatorResult) -> bool:
        """`changed == false` 即「此路不通」，立即停机。"""
        return result.changed


def has_progress(
    previous_quality: float, new_quality: float, *, min_gain: float = MIN_PROGRESS
) -> bool:
    """采纳规则：候选必须带来 ≥ 5% 的量化指标提升（research D3 / PRD 4.5.9）。"""
    return new_quality - previous_quality >= min_gain
