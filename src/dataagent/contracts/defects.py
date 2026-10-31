"""缺陷契约：11 码封闭元数据 + Defect / OperatorResult 类型。

宪法 G5 / defect-codes.md 四条硬性要求：
- 缺陷码封闭集（新增码必须走版本升级）；码 → 算子映射写死在 pipeline/repair/mapping.py。
- 每条缺陷必带 location、缺 location 不进修复队列，只能统计。
- raw_reason 仅 code = plugin_reason_unknown 时必填，上游原因原样保留。
- OperatorResult.changed == false 是停机标志（data-model.md Action.changed）——
  「此路不通」立即停机，不得重试同参数。

契约层叶子模块（仅依赖 .errors）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from pydantic import BaseModel, Field, model_validator

from dataagent.contracts.errors import DefectCode


@unique
class RepairOperator(StrEnum):
    """修复算子封闭集合（defect-codes.md「默认算子」列全部算子的并集）。"""

    OCR_PAGE = "ocr_page"
    EXTRACT_REGION = "extract_region"
    REBUILD_TABLE_CELLS = "rebuild_table_cells"
    MERGE_CROSS_PAGE_TABLE = "merge_cross_page_table"
    REORDER_BLOCKS = "reorder_blocks"
    RESEGMENT_COLUMNS = "resegment_columns"
    STRIP_HEADER_FOOTER = "strip_header_footer"
    ROTATE_PAGE = "rotate_page"
    DESKEW = "deskew"


@unique
class RepairTier(StrEnum):
    """缺陷修复档位标签（defect-codes.md「档位」列，主档位在前）。

    与 kernel.Tier 语义一致但不复用：契约层不得依赖 kernel（import-linter 分层单向），
    且 T0（原样采用）不是缺陷修复档位，故只含 T1–T3。
    """

    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


@dataclass(frozen=True)
class DefectSpec:
    """缺陷码规格：默认算子 + 档位（逐字引用 defect-codes.md「默认算子 / 档位」列）。

    operator 取「默认算子」列首项（主档算子）；None = 无修复算子（第 10/11 号）。
    tier 为主 / 次档位；空元组 = 无档位（第 10/11 号）。
    """

    operator: RepairOperator | None
    tier: tuple[RepairTier, ...]


# 11 码规格表（顺序与 defect-codes.md 缺陷码表一致）
DEFECT_SPECS: dict[DefectCode, DefectSpec] = {
    DefectCode.ENCODING_LOSS: DefectSpec(RepairOperator.OCR_PAGE, (RepairTier.T3,)),
    DefectCode.LOW_DENSITY: DefectSpec(
        RepairOperator.OCR_PAGE, (RepairTier.T3, RepairTier.T2)
    ),
    DefectCode.REGION_EMPTY: DefectSpec(RepairOperator.EXTRACT_REGION, (RepairTier.T2,)),
    DefectCode.TABLE_STRUCTURE_BROKEN: DefectSpec(
        RepairOperator.REBUILD_TABLE_CELLS, (RepairTier.T1,)
    ),
    DefectCode.TABLE_CROSS_PAGE_BREAK: DefectSpec(
        RepairOperator.MERGE_CROSS_PAGE_TABLE, (RepairTier.T1,)
    ),
    DefectCode.READING_ORDER_BROKEN: DefectSpec(
        RepairOperator.REORDER_BLOCKS, (RepairTier.T1,)
    ),
    DefectCode.COLUMN_MIXED: DefectSpec(RepairOperator.RESEGMENT_COLUMNS, (RepairTier.T2,)),
    DefectCode.HEADER_FOOTER_LEAK: DefectSpec(
        RepairOperator.STRIP_HEADER_FOOTER, (RepairTier.T1,)
    ),
    DefectCode.PAGE_ROTATED: DefectSpec(
        RepairOperator.ROTATE_PAGE, (RepairTier.T2, RepairTier.T3)
    ),
    DefectCode.PAGE_NUMBER_BROKEN: DefectSpec(None, ()),
    DefectCode.PLUGIN_REASON_UNKNOWN: DefectSpec(None, ()),
}


class DefectLocation(BaseModel):
    """缺陷定位：page_no 必填（内部一律 1-indexed），其余字段按缺陷码可选。"""

    page_no: int = Field(ge=1, description="内部页码，一律 1-indexed")
    table_index: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    item_range: tuple[int, int] | None = None
    angle: float | None = None


class Defect(BaseModel):
    """质检缺陷（qc-output.md / data-model.md Defect）。

    location 必填——缺 location 的缺陷不进修复队列，只能统计（宪法 G5）。
    """

    code: DefectCode
    location: DefectLocation
    evidence: dict[str, float | int | str] = Field(default_factory=dict)
    severity: float = Field(default=0.5, ge=0.0, le=1.0)
    suggested_action: RepairOperator | None = None
    raw_reason: str | None = None

    @model_validator(mode="after")
    def _raw_reason_only_when_unknown(self) -> Defect:
        """raw_reason 仅 code = plugin_reason_unknown 时必填，且必须原样保留。"""
        if self.code == DefectCode.PLUGIN_REASON_UNKNOWN and not self.raw_reason:
            raise ValueError("raw_reason 仅 code = plugin_reason_unknown 时必填")
        return self


class OperatorResult(BaseModel):
    """修复执行器 → 质检器消息（role-messages.md）。

    changed == false 即「此路不通」：立即停机，不得重试同参数（宪法 G5）。
    """

    doc_id: str
    page_no: int = Field(ge=1, description="内部页码，一律 1-indexed")
    candidate_artifact_id: str | None = None  # 内容哈希；无新产物时为空
    changed: bool
    cost: int = 0
    notes: str | None = None
