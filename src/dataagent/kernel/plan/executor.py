"""Plan 执行器：顺序处理、分支前向跳转、决策快照。"""

from __future__ import annotations

from collections.abc import Mapping

from dataagent.contracts.policies import StageKind
from dataagent.kernel.plan.compiler import FrozenPlan
from dataagent.kernel.plan.errors import (
    PlanBranchError,
    PlanMetricMissingError,
    PlanValidationError,
)
from dataagent.kernel.plan.guards import PlanGuardTracker
from dataagent.kernel.plan.results import (
    DecisionRecord,
    PlanGuardExceeded,
    PlanResolution,
    StageExecution,
)
from dataagent.kernel.plan.schemas import (
    PLAN_EXPRESSION_ENGINE,
    PLAN_METRICS,
    PlanBranch,
    PlanStageSpec,
)
from dataagent.platform.rules.expr import RestrictedExpressionEngine


class PlanExecutor:
    """沿冻结 Plan 求值：processing 顺序执行，decision/replan 按分支前向跳转。"""

    def __init__(
        self,
        plan: FrozenPlan,
        *,
        tracker: PlanGuardTracker | None = None,
        engine: RestrictedExpressionEngine | None = None,
    ) -> None:
        self._plan = plan
        self._tracker = tracker if tracker is not None else PlanGuardTracker(plan.guards)
        self._engine = (
            engine if engine is not None else PLAN_EXPRESSION_ENGINE
        )
        self._index = {
            stage.id: index for index, stage in enumerate(plan.spec.stages)
        }
        # 分支目标是互斥入口（类似 switch case）：线性推进时跳过，只经分支进入
        self._branch_targets = frozenset(
            branch.goto for stage in plan.spec.stages for branch in stage.branches
        )

    @property
    def tracker(self) -> PlanGuardTracker:
        return self._tracker

    def resolve(
        self,
        stats: Mapping[str, float | int],
        *,
        start_stage: str | None = None,
    ) -> PlanResolution:
        stage_list: list[StageExecution] = []
        decisions: list[DecisionRecord] = []
        index = self._start_index(start_stage)

        while index < len(self._plan.spec.stages):
            self._raise_if_stopped(stage_list, decisions)
            stage = self._plan.spec.stages[index]

            if stage.kind is StageKind.PROCESSING:
                stage_list.append(StageExecution(stage))
                index += 1
                while (
                    index < len(self._plan.spec.stages)
                    and self._plan.spec.stages[index].id in self._branch_targets
                ):
                    index += 1
                continue

            if stage.kind is StageKind.REPLAN:
                self._tracker.register_replan()
                self._raise_if_stopped(stage_list, decisions)

            record, goto = self._decide(stage, stats)
            decisions.append(record)
            index = self._index[goto]

        return PlanResolution(
            stages=tuple(stage_list),
            decisions=tuple(decisions),
        )

    def _start_index(self, start_stage: str | None) -> int:
        if start_stage is None:
            return 0
        index = self._index.get(start_stage)
        if index is None:
            raise PlanValidationError(f"start_stage 不存在：{start_stage!r}")
        return index

    def _decide(
        self, stage: PlanStageSpec, stats: Mapping[str, float | int]
    ) -> tuple[DecisionRecord, str]:
        if stage.on is None:  # PlanStageSpec 校验保证非空
            raise PlanValidationError(f"{stage.id} 缺少 on")
        metric = stage.on.metric
        if metric not in stats:
            raise PlanMetricMissingError(f"决策缺少统计指标：{metric!r}")

        snapshot = {key: value for key, value in stats.items() if key in PLAN_METRICS}
        branch_taken = self._select_branch(stage, stats)
        return (
            DecisionRecord(
                plan_version=self._plan.spec.plan_version,
                stage_id=stage.id,
                metric_snapshot=snapshot,
                branch_taken=branch_taken.goto,
            ),
            branch_taken.goto,
        )

    def _select_branch(
        self, stage: PlanStageSpec, stats: Mapping[str, float | int]
    ) -> PlanBranch:
        for branch in stage.branches:
            if branch.else_ is not None:
                return branch
            if bool(self._engine.evaluate(branch.when, stats)):
                return branch
        raise PlanBranchError(f"无分支命中且缺少 else 兜底：{stage.id}")

    def _raise_if_stopped(
        self,
        stages: list[StageExecution],
        decisions: list[DecisionRecord],
    ) -> None:
        reason = self._tracker.stop_reason()
        if reason is None:
            return
        limit = int(getattr(self._tracker.guards, reason))
        raise PlanGuardExceeded(
            reason,
            limit=limit,
            partial=PlanResolution(
                stages=tuple(stages),
                decisions=tuple(decisions),
                stopped_by=reason,
            ),
        )
