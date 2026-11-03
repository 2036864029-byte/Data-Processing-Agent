"""PlanPolicy 模型实现：NL → 受约束 Plan（M3 / FR-026）。

职责边界：
- 本模块只负责“自然语言 + 任务统计 → 结构化 Plan”；
- 模型客户端通过 `PlanDraftModel` Protocol 注入，便于测试与替换 provider；
- 所有输出必须经 `parse_plan` 校验并冻结；未知指标 / 自由图结构在生成期拒绝；
- 每次尝试计一次模型调用，受 `PlanGuardTracker` 硬上限约束。

本文件族（`model_*.py`）是唯一允许引入模型 SDK 的位置（宪法门禁）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from dataagent.contracts.policies import Plan as ContractPlan
from dataagent.kernel.plan import (
    PLAN_METRICS,
    FrozenPlan,
    PlanGuardExceeded,
    PlanGuardTracker,
    PlanResolution,
    PlanValidationError,
    parse_plan,
)


class PlanCompileError(ValueError):
    """模型多次输出仍不满足 Plan 约束。"""


class PlanDraftModel(Protocol):
    """模型客户端：按指令返回结构化 Plan 草稿（JSON 对象）。"""

    def draft(
        self,
        *,
        instruction: str,
        nl: str,
        task_stats: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


def build_plan_instruction(task_stats: Mapping[str, Any]) -> str:
    """构造严格的 Plan 生成指令（封闭 schema，禁止自由文本/自由图）。"""
    metrics = ", ".join(sorted(PLAN_METRICS))
    stats = json.dumps(dict(task_stats), ensure_ascii=False, sort_keys=True)
    return f"""你是 DataAgent 的任务级规划器。把用户自然语言策略编译为受约束的执行计划。

规则：
1. 只输出一个 JSON 对象，不要 Markdown、不要解释文字。
2. stage kind 只能是 processing / decision / replan。
3. 分支只能用受限 JSONLogic：when 为对象（如 {{">=": [{{"var": "stp_rate"}}, 0.85]}}）；
   每条分支必须有 goto；else 只能出现在最后一条。
4. 分支表达式只能引用以下统计指标：{metrics}。
5. 分支只能跳转到已定义且位于其后的 stage（禁止回跳/自由图）。
6. guards 默认 max_model_calls=8 / max_replans=3 / max_wall_time_s=3600。
7. 典型结构：probe（1-100 页）→ decision（按 stp_rate 分支）→ bulk_fast / bulk_safe。

任务统计快照：{stats}

输出 schema：
{{"version": 1, "stages": [{{"id": "probe", "kind": "processing",
  "scope": {{"pages": "1-100"}}, "strategy": "quality"}},
  {{"id": "decide", "kind": "decision",
  "on": {{"metric": "stp_rate", "window": "probe"}},
  "branches": [{{"when": {{">=": [{{"var": "stp_rate"}}, 0.85]}}, "goto": "bulk_fast"}},
               {{"else": true, "goto": "bulk_safe"}}]}},
  {{"id": "bulk_fast", "kind": "processing",
    "scope": {{"pages": "101-"}}, "strategy": "fast"}},
  {{"id": "bulk_safe", "kind": "processing",
    "scope": {{"pages": "101-"}}, "strategy": "quality"}}]],
 "guards": {{"max_model_calls": 8, "max_replans": 3, "max_wall_time_s": 3600}}}}
"""


class ModelPlanPolicy:
    """PlanPolicy 模型实现（可注入模型客户端，输出冻结 Plan）。"""

    def __init__(
        self,
        model: PlanDraftModel,
        *,
        max_attempts: int = 2,
        budget: PlanGuardTracker | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须 ≥ 1")
        self._model = model
        self._max_attempts = max_attempts
        self._budget = budget

    def compile(self, nl: str, task_stats: dict[str, Any]) -> ContractPlan:
        """PlanPolicy Protocol 适配：返回策略层最小视图。"""
        return self.compile_frozen(nl, task_stats).contract_plan()

    def compile_frozen(
        self, nl: str, task_stats: Mapping[str, Any]
    ) -> FrozenPlan:
        """NL → 校验 → 冻结；失败重试，最终抛 PlanCompileError。"""
        instruction = build_plan_instruction(task_stats)
        errors: list[str] = []
        for _ in range(self._max_attempts):
            self._charge_model_call()
            draft = self._model.draft(
                instruction=instruction, nl=nl, task_stats=task_stats
            )
            try:
                payload = dict(draft)
                payload.setdefault("version", 1)
                payload["created_by"] = "model"
                return parse_plan(payload)
            except PlanValidationError as exc:
                errors.append(str(exc))
        raise PlanCompileError(
            "模型输出不满足 Plan 约束：" + "；".join(errors[-2:])
        )

    def _charge_model_call(self) -> None:
        if self._budget is None:
            return
        reason = self._budget.stop_reason()
        if reason is None and (
            self._budget.model_calls >= self._budget.guards.max_model_calls
        ):
            reason = "max_model_calls"
        if reason is not None:
            limit = int(getattr(self._budget.guards, reason))
            raise PlanGuardExceeded(
                reason,
                limit=limit,
                partial=PlanResolution(stages=(), decisions=()),
            )
        self._budget.record_model_call()
