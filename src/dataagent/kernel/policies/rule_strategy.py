"""StrategyPolicy 规则实现（P0：阈值；M3 才可替换为模型实现）。

优先级（确定性）：
1. 预算耗尽 → ABORT；
2. 分类置信度 < 0.85（判不准类型，抽页没用）或失败页 ≥ 40% → 整本 OCR；
3. 其余 → 降档（保守降低性能档位，控制成本）。
"""

from __future__ import annotations

from typing import Any

from dataagent.contracts.policies import Strategy

DEFAULT_CLASS_CONFIDENCE_THRESHOLD: float = 0.85
DEFAULT_FAILURE_RATIO_THRESHOLD: float = 0.40


class RuleStrategyPolicy:
    def __init__(
        self,
        *,
        class_confidence_threshold: float = DEFAULT_CLASS_CONFIDENCE_THRESHOLD,
        failure_ratio_threshold: float = DEFAULT_FAILURE_RATIO_THRESHOLD,
    ) -> None:
        self._class_confidence_threshold = class_confidence_threshold
        self._failure_ratio_threshold = failure_ratio_threshold

    def select(self, task_stats: dict[str, Any]) -> Strategy:
        if bool(task_stats.get("budget_exhausted", False)):
            return Strategy.ABORT

        class_confidence = float(task_stats.get("class_confidence", 1.0))
        failed_ratio = float(task_stats.get("failed_page_ratio", 0.0))
        if (
            class_confidence < self._class_confidence_threshold
            or failed_ratio >= self._failure_ratio_threshold
        ):
            return Strategy.WHOLE_BOOK_OCR

        return Strategy.DOWNGRADE
