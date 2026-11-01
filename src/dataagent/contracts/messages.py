"""角色间消息契约（contracts/role-messages.md）。

四角色仅三条消息：ParseRequest（调度器 → 解析器）、PageReport（质检器 → 调度器 / 修复执行器）、
OperatorResult（修复执行器 → 质检器，见 contracts/defects.py）。

契约层叶子模块（仅依赖 .defects）。
"""

from __future__ import annotations

from enum import StrEnum, unique
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataagent.contracts.defects import Defect, RepairOperator


@unique
class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


class ParseRequest(BaseModel):
    """调度器 → 解析器：一次批级解析请求。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    doc_id: str
    page_range: tuple[int, int]
    engine: str
    params_hash: str
    rulepack_hash: str
    resource_hint: str
    doc_affinity_key: str

    @model_validator(mode="after")
    def _validate_page_range(self) -> Self:
        """page_range 为内部 1-indexed 闭区间，且 start ≤ end。"""
        start, end = self.page_range
        if start < 1 or end < start:
            raise ValueError("page_range 必须是 1-indexed 闭区间且 start ≤ end")
        return self


class PageReport(BaseModel):
    """质检器 → 调度器 / 修复执行器：页级质检报告（qc-output.md）。"""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    page_no: int = Field(ge=1, description="内部页码，一律 1-indexed")
    verdict: Verdict
    extract_quality: float = Field(ge=0.0, le=1.0)
    defects: list[Defect] = Field(default_factory=list)
    attribution: str | None = None
    recommended_action: RepairOperator | None = None
    qc_confidence: float = Field(ge=0.0, le=1.0)
