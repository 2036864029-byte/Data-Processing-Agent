"""B-PLAN：任务级执行规划（FR-026 / PRD V2.3 4.1.4 规则 7–9）。

- `stages` 仅允许 3 种 kind（processing / decision / replan），禁止自由图结构：
  分支只能引用已定义且位于其后的 stage（前向跳转，天然无环）。
- 分支表达式复用 B8 受限表达式引擎（`platform/rules/expr.py`），字段白名单仅限
  统计指标；引用未知指标在**生成期**（`parse_plan`）即拒绝。
- `guards` 三项硬上限（模型调用 / 重规划 / 墙钟时间）任一超出即停止 Plan，
  调用方交付带缺口结果（`PlanGuardExceeded.partial` 携带已执行部分）。
- Plan 解析即冻结（不可变模型），同版本重放逐字一致；`replan_with` 产生新
  `plan_version` 并可用 `diff` 定位变化。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError

from dataagent.contracts.policies import Plan as ContractPlan
from dataagent.contracts.policies import PlanStage as ContractPlanStage
from dataagent.contracts.policies import StageKind
from dataagent.platform.rules.expr import (
    ExpressionError,
    FieldWhitelist,
    RestrictedExpressionEngine,
)

# 分支表达式白名单：仅统计指标（PRD 规则 8）
PLAN_METRICS: Final[frozenset[str]] = frozenset(
    {
        "stp_rate",
        "pages_total",
        "pages_processed",
        "pages_failed",
        "gap_ratio",
        "avg_extract_quality",
        "elapsed_s",
        "model_calls",
        "cost_per_page",
    }
)

# processing stage 允许的档位策略（PRD 4.1.4 示例 + contracts Strategy）
STAGE_STRATEGIES: Final[frozenset[str]] = frozenset(
    {"quality", "balanced", "fast", "whole_book_ocr", "downgrade", "abort"}
)

DEFAULT_MAX_MODEL_CALLS: Final[int] = 8
DEFAULT_MAX_REPLANS: Final[int] = 3
DEFAULT_MAX_WALL_TIME_S: Final[int] = 3600

_SCOPE_PATTERN = re.compile(r"^(?P<start>\d+)(?:-(?P<end>\d*))?$")
_ENGINE: Final[RestrictedExpressionEngine] = RestrictedExpressionEngine(
    whitelist=FieldWhitelist(allowed_paths=PLAN_METRICS)
)


class PlanError(ValueError):
    """Plan 相关错误基类。"""


class PlanValidationError(PlanError):
    """Plan 结构 / 表达式 / 引用不合法（生成期拒绝）。"""


class PlanMetricMissingError(PlanError):
    """执行期缺少决策所需统计指标。"""


class PlanBranchError(PlanError):
    """没有分支命中且无 else 兜底。"""


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


class PlanGuards(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_model_calls: int = Field(default=DEFAULT_MAX_MODEL_CALLS, gt=0)
    max_replans: int = Field(default=DEFAULT_MAX_REPLANS, gt=0)
    max_wall_time_s: int = Field(default=DEFAULT_MAX_WALL_TIME_S, gt=0)


class PlanScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pages: str

    @field_validator("pages")
    @classmethod
    def _validate_pages(cls, value: str) -> str:
        match = _SCOPE_PATTERN.match(value.strip())
        if match is None:
            raise ValueError(f"pages 范围不合法：{value!r}（示例 '1-100' / '101-'）")
        start = int(match.group("start"))
        if start < 1:
            raise ValueError("pages 范围起始页必须 ≥ 1")
        end_text = match.group("end")
        if end_text and int(end_text) < start:
            raise ValueError("pages 范围起始页不能大于结束页")
        return value.strip()

    def resolve_bounds(self, total_pages: int) -> tuple[int, int]:
        """在给定总页数下解析为 1-indexed 闭区间。"""
        match = _SCOPE_PATTERN.match(self.pages)
        if match is None:  # 校验已保证可解析
            raise PlanValidationError(f"pages 范围不合法：{self.pages!r}")
        start = int(match.group("start"))
        end_text = match.group("end")
        end = int(end_text) if end_text else total_pages
        return start, min(end, total_pages)

    def contains(self, page: int, total_pages: int) -> bool:
        start, end = self.resolve_bounds(total_pages)
        return start <= page <= end


class PlanDecisionOn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str
    window: str | None = None

    @field_validator("metric")
    @classmethod
    def _validate_metric(cls, value: str) -> str:
        if value not in PLAN_METRICS:
            raise ValueError(f"决策指标必须是统计指标（白名单）：{value!r}")
        return value


class PlanBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    when: Any = None
    else_: Literal[True] | None = Field(default=None, alias="else")
    goto: str

    @model_validator(mode="after")
    def _exactly_one_condition(self) -> Self:
        has_when = self.when is not None
        has_else = self.else_ is not None
        if has_when == has_else:
            raise ValueError("每条分支必须且只能声明 when 或 else 之一")
        return self


class PlanStageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kind: StageKind
    scope: PlanScope | None = None
    strategy: str | None = None
    on: PlanDecisionOn | None = None
    branches: tuple[PlanBranch, ...] = ()

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> Self:
        if self.kind is StageKind.PROCESSING:
            if self.on is not None or self.branches:
                raise ValueError("processing stage 不得包含 on / branches")
            if self.strategy is not None and self.strategy not in STAGE_STRATEGIES:
                raise ValueError(f"未知 stage strategy：{self.strategy!r}")
            return self

        if self.on is None:
            raise ValueError(f"{self.kind.value} stage 必须声明 on")
        if not self.branches:
            raise ValueError(f"{self.kind.value} stage 必须声明 branches（分支）")
        if self.scope is not None or self.strategy is not None:
            raise ValueError(f"{self.kind.value} stage 不得包含 scope / strategy")
        for branch in self.branches[:-1]:
            if branch.else_ is not None:
                raise ValueError("else 分支只能出现在 branches 最后一条")
        return self


class PlanSpec(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True
    )

    plan_version: int = Field(alias="version", ge=1)
    stages: tuple[PlanStageSpec, ...] = Field(min_length=1)
    guards: PlanGuards = Field(default_factory=PlanGuards)
    created_by: Literal["rule_template", "model"] = "rule_template"

    @model_validator(mode="after")
    def _validate_graph(self) -> Self:
        seen: set[str] = set()
        for stage in self.stages:
            if stage.id in seen:
                raise ValueError(f"stage id 重复：{stage.id!r}")
            seen.add(stage.id)

        index_by_id = {stage.id: index for index, stage in enumerate(self.stages)}
        for index, stage in enumerate(self.stages):
            for branch in stage.branches:
                self._validate_expression(stage, branch)
                target = index_by_id.get(branch.goto)
                if target is None:
                    raise ValueError(
                        f"分支 goto 引用不存在的 stage：{branch.goto!r}（{stage.id}）"
                    )
                if target <= index:
                    raise ValueError(
                        f"分支只能向前跳转：{stage.id} → {branch.goto}"
                    )
            if stage.on is not None and stage.on.window is not None:
                window_index = index_by_id.get(stage.on.window)
                if window_index is None:
                    raise ValueError(
                        f"决策 window 引用不存在的 stage：{stage.on.window!r}"
                    )
                if window_index >= index:
                    raise ValueError(
                        f"决策 window 必须引用更早的 stage：{stage.id} < {stage.on.window}"
                    )
        return self

    @staticmethod
    def _validate_expression(stage: PlanStageSpec, branch: PlanBranch) -> None:
        if branch.when is None:
            return
        try:
            _ENGINE.validate(branch.when)
        except ExpressionError as exc:
            raise ValueError(f"分支表达式不合法（{stage.id}）：{exc}") from exc


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


@dataclass(frozen=True, slots=True)
class FrozenPlan:
    """冻结后的 Plan：不可变 + 规范化指纹（同版本重放逐字一致）。"""

    spec: PlanSpec
    canonical_json: str
    fingerprint: str
    frozen_at: str

    @property
    def version(self) -> int:
        return self.spec.plan_version

    @property
    def guards(self) -> PlanGuards:
        return self.spec.guards

    @property
    def stages(self) -> tuple[PlanStageSpec, ...]:
        return self.spec.stages

    def contract_plan(self) -> ContractPlan:
        return ContractPlan(
            plan_version=self.spec.plan_version,
            stages=tuple(
                ContractPlanStage(
                    id=stage.id,
                    kind=stage.kind,
                    scope=stage.scope.pages if stage.scope is not None else None,
                    strategy=stage.strategy,
                )
                for stage in self.spec.stages
            ),
            created_by=self.spec.created_by,
        )

    def replan_with(
        self, stages: Sequence[Mapping[str, Any] | PlanStageSpec]
    ) -> FrozenPlan:
        """重规划：版本 +1、沿用 guards，产生可 diff 的新冻结 Plan。"""
        payload = self.spec.model_dump(mode="json", by_alias=True)
        payload["version"] = self.spec.plan_version + 1
        payload["stages"] = [
            stage.model_dump(mode="json", by_alias=True)
            if isinstance(stage, PlanStageSpec)
            else dict(stage)
            for stage in stages
        ]
        return parse_plan(payload)

    def diff(self, other: FrozenPlan) -> tuple[str, ...]:
        """返回与另一版本相比新增 / 删除 / 内容变化的 stage id（排序）。"""
        mine = {stage.id: stage for stage in self.spec.stages}
        theirs = {stage.id: stage for stage in other.spec.stages}
        changed = {
            stage_id
            for stage_id in mine.keys() | theirs.keys()
            if mine.get(stage_id) != theirs.get(stage_id)
        }
        return tuple(sorted(changed))


def parse_plan(data: Mapping[str, Any]) -> FrozenPlan:
    """解析 + 校验 + 冻结；任何结构 / 引用 / 表达式问题在生成期拒绝。"""
    try:
        spec = PlanSpec.model_validate(data)
    except PydanticValidationError as exc:
        raise PlanValidationError(str(exc)) from exc
    canonical = json.dumps(
        spec.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return FrozenPlan(
        spec=spec,
        canonical_json=canonical,
        fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        frozen_at=datetime.now(UTC).isoformat(),
    )


@dataclass(slots=True)
class PlanGuardTracker:
    """guards 计数器；跨重规划共享同一实例（模型调用 / 重规划 / 时间累计）。"""

    guards: PlanGuards
    clock: Callable[[], float] = time.monotonic
    _started_at: float = field(init=False)
    _model_calls: int = field(default=0, init=False)
    _replans: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._started_at = self.clock()

    @property
    def model_calls(self) -> int:
        return self._model_calls

    @property
    def replans(self) -> int:
        return self._replans

    @property
    def elapsed_s(self) -> float:
        return self.clock() - self._started_at

    def record_model_call(self, count: int = 1) -> None:
        self._model_calls += count

    def register_replan(self) -> None:
        self._replans += 1

    def stop_reason(self) -> str | None:
        """返回触发的 guard 字段名；无触发返回 None。"""
        if self._model_calls > self.guards.max_model_calls:
            return "max_model_calls"
        if self._replans > self.guards.max_replans:
            return "max_replans"
        if self.elapsed_s > self.guards.max_wall_time_s:
            return "max_wall_time_s"
        return None


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
        self._engine = engine if engine is not None else _ENGINE
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
