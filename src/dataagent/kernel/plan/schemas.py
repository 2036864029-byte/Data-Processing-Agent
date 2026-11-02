"""Plan schema：结构模型与字段校验（纯数据，不含执行）。"""

from __future__ import annotations

import re
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dataagent.contracts.policies import StageKind
from dataagent.kernel.plan.errors import PlanValidationError
from dataagent.platform.rules.expr import (
    ExpressionError,
    FieldWhitelist,
    RestrictedExpressionEngine,
)

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
PLAN_EXPRESSION_ENGINE: Final[RestrictedExpressionEngine] = RestrictedExpressionEngine(
    whitelist=FieldWhitelist(allowed_paths=PLAN_METRICS)
)


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
            PLAN_EXPRESSION_ENGINE.validate(branch.when)
        except ExpressionError as exc:
            raise ValueError(f"分支表达式不合法（{stage.id}）：{exc}") from exc
