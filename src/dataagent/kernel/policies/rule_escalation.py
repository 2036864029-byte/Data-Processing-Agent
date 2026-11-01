"""EscalationPolicy 规则实现：何时升级人工（PRD 4.5.3 动作 F）。

P0 规则：预算耗尽，或归因置信度不足以自动决策 → 升级人工 + 诊断卡。
"""

from __future__ import annotations

from dataagent.contracts.policies import Attribution, Budget

DEFAULT_MIN_ATTRIBUTION_CONFIDENCE: float = 0.5


class RuleEscalationPolicy:
    def __init__(
        self,
        *,
        min_attribution_confidence: float = DEFAULT_MIN_ATTRIBUTION_CONFIDENCE,
    ) -> None:
        self._min_attribution_confidence = min_attribution_confidence

    def escalate(self, budget: Budget, attribution: Attribution) -> bool:
        return (
            budget.exhausted
            or attribution.confidence < self._min_attribution_confidence
        )
