"""ReplanPolicy 规则实现（P0：阈值触发；M3 才可替换为模型实现）。

阈值：探路统计中失败页占比 ≥ 40% → 整本转 OCR（policies.md / T053 示例）。
守卫：重规划次数达到 `max_replans` 即不再产出新 Plan（不允许无限重规划）。
"""

from __future__ import annotations

from typing import Any

from dataagent.contracts.policies import Plan, PlanStage, StageKind, Strategy

DEFAULT_FAILURE_RATIO_THRESHOLD: float = 0.40
DEFAULT_MAX_REPLANS: int = 3


class RuleReplanPolicy:
    def __init__(
        self,
        *,
        failure_ratio_threshold: float = DEFAULT_FAILURE_RATIO_THRESHOLD,
        max_replans: int = DEFAULT_MAX_REPLANS,
    ) -> None:
        self._failure_ratio_threshold = failure_ratio_threshold
        self._max_replans = max_replans

    def decide(self, exec_stats: dict[str, Any]) -> Plan | None:
        replans_used = int(exec_stats.get("replans_used", 0))
        max_replans = int(exec_stats.get("max_replans", self._max_replans))
        if replans_used >= max_replans:
            return None

        failed_ratio = float(exec_stats.get("failed_page_ratio", 0.0))
        if failed_ratio < self._failure_ratio_threshold:
            return None

        plan_version = int(exec_stats.get("plan_version", 1)) + 1
        return Plan(
            plan_version=plan_version,
            stages=(
                PlanStage(
                    id="whole_book_ocr",
                    kind=StageKind.PROCESSING,
                    scope="1-",
                    strategy=Strategy.WHOLE_BOOK_OCR,
                ),
            ),
            created_by="rule_template",
        )
