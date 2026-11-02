"""Plan 执行结果：阶段执行、决策记录、守卫异常。"""

from __future__ import annotations

from dataclasses import dataclass

from dataagent.kernel.plan.errors import PlanError
from dataagent.kernel.plan.schemas import PlanStageSpec


@dataclass(frozen=True, slots=True)
class PlanResolution:
    """一次 Plan 求值结果（阶段序列 + 决策记录），同版本重放逐字一致。"""

    stages: tuple[StageExecution, ...]
    decisions: tuple[DecisionRecord, ...]
    stopped_by: str | None = None


class PlanGuardExceeded(PlanError):
    """guards 硬上限触发：停止 Plan，交付带缺口结果。"""

    def __init__(self, guard: str, *, limit: int, partial: PlanResolution) -> None:
        super().__init__(f"guards 硬上限触发：{guard} > {limit}，停止 Plan 并交付带缺口结果")
        self.guard = guard
        self.limit = limit
        self.partial = partial


@dataclass(frozen=True, slots=True)
class StageExecution:
    stage: PlanStageSpec

    @property
    def id(self) -> str:
        return self.stage.id


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """每次 decision / replan stage 求值一条（可审计、可重放）。"""

    plan_version: int
    stage_id: str
    metric_snapshot: dict[str, float | int]
    branch_taken: str
