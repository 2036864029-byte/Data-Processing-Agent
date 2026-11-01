"""算子契约（B6）：同进程纯函数 + 统一签名（PRD 4.5.8）。

算子签名：`operator(artifact, defect, ctx) -> OperatorOutcome`
- 算子幂等：同输入两次调用结果一致；
- 不得修改原产物：返回新 PageArtifact（`candidate`），原对象不变；
- T1 后处理 cost 固定为 0。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.defects import Defect


@dataclass(frozen=True, slots=True)
class OperatorContext:
    doc_id: str
    page_no: int
    params_hash: str = ""
    next_artifact: PageArtifact | None = None


@dataclass(frozen=True, slots=True)
class OperatorOutcome:
    candidate: PageArtifact | None
    changed: bool
    cost: int
    notes: str


OperatorFn = Callable[[PageArtifact, Defect, OperatorContext], OperatorOutcome]
