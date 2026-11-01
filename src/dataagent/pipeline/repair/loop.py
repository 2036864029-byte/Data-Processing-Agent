"""增强回路（B6/T106 / PRD 4.5.9）：同码唯一 + 三层停机 + 量化采纳。

- 同码唯一：同一缺陷码在同一页只修一次，修不动直接升级，不换花样。
- `changed == false` 立即放弃该算子，不重试同参数。
- 三层预算：轮次 / 耗时 / 成本（BudgetTracker）任一耗尽即升级。
- 采纳规则：候选必须带来 ≥5% 质量提升 **且** 缺陷数减少，否则丢弃候选。
- 归复杂页后不再动作（调用方写 PageGap，交聚包或人工）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.defects import Defect, RepairOperator
from dataagent.contracts.errors import DefectCode
from dataagent.kernel.budget import BudgetExceeded, BudgetTracker
from dataagent.kernel.budget import Tier as BudgetTier
from dataagent.pipeline.repair.mapping import operator_for
from dataagent.pipeline.repair.operators import (
    OperatorContext,
    OperatorFn,
    run_operator,
)

DEFAULT_MAX_ROUNDS: Final[int] = 3
DEFAULT_MAX_DURATION_S: Final[float] = 2.0
MIN_QUALITY_GAIN: Final[float] = 0.05


@dataclass(frozen=True, slots=True)
class PageProbe:
    """候选产物复检结果（只算该缺陷相关指标，由调用方注入）。"""

    quality: float
    defects: tuple[Defect, ...]


QualityProbe = Callable[[PageArtifact], PageProbe]


@dataclass(frozen=True, slots=True)
class LoopLimits:
    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_duration_s: float = DEFAULT_MAX_DURATION_S
    min_quality_gain: float = MIN_QUALITY_GAIN


@dataclass(frozen=True, slots=True)
class ActionRecord:
    code: DefectCode
    operator: RepairOperator
    changed: bool
    cost: int
    notes: str


@dataclass(frozen=True, slots=True)
class EnhancementOutcome:
    artifact: PageArtifact
    remaining_defects: tuple[Defect, ...]
    actions: tuple[ActionRecord, ...]
    escalated: bool
    escalation_reason: str | None
    cost: int


def run_enhancement(
    artifact: PageArtifact,
    defects: Sequence[Defect],
    *,
    baseline_quality: float,
    probe: QualityProbe,
    operators: Mapping[RepairOperator, OperatorFn] | None = None,
    limits: LoopLimits | None = None,
    budget: BudgetTracker | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> EnhancementOutcome:
    limits = limits if limits is not None else LoopLimits()
    current = artifact
    remaining = tuple(defects)
    current_quality = baseline_quality
    tried: set[DefectCode] = set()
    actions: list[ActionRecord] = []
    total_cost = 0
    rounds = 0
    escalated = False
    escalation_reason: str | None = None
    start = clock()

    while remaining:
        if rounds >= limits.max_rounds:
            escalated, escalation_reason = True, "rounds_exhausted"
            break
        if clock() - start > limits.max_duration_s:
            escalated, escalation_reason = True, "time_budget"
            break

        defect = next(
            (item for item in remaining if item.code not in tried), None
        )
        if defect is None:
            escalated, escalation_reason = True, "same_code_exhausted"
            break
        tried.add(defect.code)

        operator = operator_for(defect.code)
        if operator is None:
            continue  # 无算子：仅统计（page_number_broken / plugin_reason_unknown）

        outcome = run_operator(
            operator,
            current,
            defect,
            OperatorContext(current.doc_id, current.page_no),
            operators=operators,
        )
        rounds += 1
        total_cost += outcome.cost
        actions.append(
            ActionRecord(
                code=defect.code,
                operator=operator,
                changed=outcome.changed,
                cost=outcome.cost,
                notes=outcome.notes,
            )
        )

        if not outcome.changed or outcome.candidate is None:
            continue  # 此路不通：不重试同参数

        if budget is not None:
            try:
                budget.record(BudgetTier.T1)
            except BudgetExceeded:
                escalated, escalation_reason = True, "budget_exhausted"
                break

        candidate_probe = probe(outcome.candidate)
        quality_gain = candidate_probe.quality - current_quality
        if (
            quality_gain + 1e-9 < limits.min_quality_gain
            or len(candidate_probe.defects) >= len(remaining)
        ):
            continue  # 无进展：丢弃候选，该算子判无效

        current = outcome.candidate
        current_quality = candidate_probe.quality
        remaining = candidate_probe.defects

    return EnhancementOutcome(
        artifact=current,
        remaining_defects=remaining,
        actions=tuple(actions),
        escalated=escalated,
        escalation_reason=escalation_reason,
        cost=total_cost,
    )
