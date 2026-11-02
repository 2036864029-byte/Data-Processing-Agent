"""Plan 领域包：schema、编译/冻结、执行、守卫与结果。"""

from __future__ import annotations

from dataagent.kernel.plan.compiler import FrozenPlan, parse_plan
from dataagent.kernel.plan.errors import (
    PlanBranchError,
    PlanError,
    PlanMetricMissingError,
    PlanValidationError,
)
from dataagent.kernel.plan.executor import PlanExecutor
from dataagent.kernel.plan.guards import PlanGuardTracker
from dataagent.kernel.plan.results import (
    DecisionRecord,
    PlanGuardExceeded,
    PlanResolution,
    StageExecution,
)
from dataagent.kernel.plan.schemas import (
    DEFAULT_MAX_MODEL_CALLS,
    DEFAULT_MAX_REPLANS,
    DEFAULT_MAX_WALL_TIME_S,
    PLAN_EXPRESSION_ENGINE,
    PLAN_METRICS,
    STAGE_STRATEGIES,
    PlanBranch,
    PlanDecisionOn,
    PlanGuards,
    PlanScope,
    PlanSpec,
    PlanStageSpec,
)

__all__ = [
    "DEFAULT_MAX_MODEL_CALLS",
    "DEFAULT_MAX_REPLANS",
    "DEFAULT_MAX_WALL_TIME_S",
    "DecisionRecord",
    "FrozenPlan",
    "PLAN_EXPRESSION_ENGINE",
    "PLAN_METRICS",
    "STAGE_STRATEGIES",
    "PlanBranch",
    "PlanBranchError",
    "PlanDecisionOn",
    "PlanError",
    "PlanExecutor",
    "PlanGuardExceeded",
    "PlanGuardTracker",
    "PlanGuards",
    "PlanMetricMissingError",
    "PlanResolution",
    "PlanScope",
    "PlanSpec",
    "PlanStageSpec",
    "PlanValidationError",
    "StageExecution",
    "parse_plan",
]
