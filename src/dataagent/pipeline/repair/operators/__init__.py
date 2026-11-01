"""T1 后处理算子库（B6）。"""

from __future__ import annotations

from collections.abc import Mapping

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.defects import Defect, RepairOperator
from dataagent.pipeline.repair.operators.base import (
    OperatorContext,
    OperatorFn,
    OperatorOutcome,
)
from dataagent.pipeline.repair.operators.markdown import OPERATORS


def run_operator(
    operator: RepairOperator,
    artifact: PageArtifact,
    defect: Defect,
    context: OperatorContext,
    *,
    operators: Mapping[RepairOperator, OperatorFn] | None = None,
) -> OperatorOutcome:
    """执行算子；`operators` 可注入（测试 / 未来插件化），默认用内置库。"""
    table = OPERATORS if operators is None else operators
    return table[operator](artifact, defect, context)


__all__ = [
    "OPERATORS",
    "OperatorContext",
    "OperatorFn",
    "OperatorOutcome",
    "run_operator",
]
