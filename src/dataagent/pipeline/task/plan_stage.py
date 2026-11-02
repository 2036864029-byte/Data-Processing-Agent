"""Plan 阶段执行：探路、分支求值与批量阶段派发。

只负责“按冻结 Plan 决定处理哪些页”，实际页处理通过回调注入。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dataagent.contracts.policies import StageKind
from dataagent.kernel.plan import (
    DecisionRecord,
    FrozenPlan,
    PlanExecutor,
    PlanGuardExceeded,
    PlanResolution,
    PlanStageSpec,
    StageExecution,
)
from dataagent.pipeline.task.context import DocumentContext
from dataagent.pipeline.task.results import TaskStats

ProcessStage = Callable[[DocumentContext, Sequence[int], str], None]
ContextStats = Callable[[Sequence[DocumentContext], int], TaskStats]


@dataclass(frozen=True, slots=True)
class PlanExecution:
    resolution: PlanResolution | None
    decisions: tuple[DecisionRecord, ...]
    stopped_by: str | None


class PlanStageService:
    def __init__(
        self,
        *,
        plan: FrozenPlan | None,
        default_strategy: str,
        process_stage: ProcessStage,
        context_stats: ContextStats,
    ) -> None:
        self._plan = plan
        self._default_strategy = default_strategy
        self._process_stage = process_stage
        self._context_stats = context_stats

    def execute(self, contexts: Sequence[DocumentContext], total_pages: int) -> PlanExecution:
        if self._plan is None:
            self._run_stage(contexts, None, total_pages, self._default_strategy)
            return PlanExecution(None, (), None)

        plan = self._plan
        probe_stages = _probe_stages(plan)
        for stage in probe_stages:
            self._run_stage(contexts, stage, total_pages, stage.strategy or "balanced")

        if not any(stage.kind is not StageKind.PROCESSING for stage in plan.spec.stages):
            for stage in plan.spec.stages[len(probe_stages) :]:
                self._run_stage(contexts, stage, total_pages, stage.strategy or "balanced")
            stages = tuple(
                StageExecution(stage)
                for stage in plan.spec.stages
                if stage.kind is StageKind.PROCESSING
            )
            return PlanExecution(PlanResolution(stages=stages, decisions=()), (), None)

        stats = self._context_stats(contexts, total_pages)
        try:
            resolution = PlanExecutor(plan).resolve(stats.as_plan_stats())
        except PlanGuardExceeded as exc:
            return PlanExecution(exc.partial, exc.partial.decisions, exc.guard)

        executed = {stage.id for stage in probe_stages}
        for execution in resolution.stages:
            if execution.id in executed:
                continue
            self._run_stage(
                contexts,
                execution.stage,
                total_pages,
                execution.stage.strategy or "balanced",
            )
        return PlanExecution(resolution, resolution.decisions, None)

    def _run_stage(
        self,
        contexts: Sequence[DocumentContext],
        stage: PlanStageSpec | None,
        total_pages: int,
        strategy: str,
    ) -> None:
        for context, pages in _stage_pages(contexts, stage, total_pages):
            if pages:
                self._process_stage(context, pages, strategy)


def _probe_stages(plan: FrozenPlan) -> list[PlanStageSpec]:
    probe: list[PlanStageSpec] = []
    for stage in plan.spec.stages:
        if stage.kind is not StageKind.PROCESSING:
            break
        probe.append(stage)
    return probe


def _stage_pages(
    contexts: Sequence[DocumentContext],
    stage: PlanStageSpec | None,
    total_pages: int,
) -> list[tuple[DocumentContext, list[int]]]:
    result: list[tuple[DocumentContext, list[int]]] = []
    offset = 0
    for context in contexts:
        pages: list[int] = []
        for page in context.universe:
            offset += 1
            if page in context.processed:
                continue
            if stage is None or stage.scope is None or stage.scope.contains(offset, total_pages):
                pages.append(page)
        result.append((context, pages))
    return result
