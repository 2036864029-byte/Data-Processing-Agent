"""Plan 编译：解析、冻结、规范化指纹与版本重规划。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from dataagent.contracts.policies import Plan as ContractPlan
from dataagent.contracts.policies import PlanStage as ContractPlanStage
from dataagent.kernel.plan.errors import PlanValidationError
from dataagent.kernel.plan.schemas import PlanGuards, PlanSpec, PlanStageSpec


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
