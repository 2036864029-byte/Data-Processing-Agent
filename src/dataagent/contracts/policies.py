"""决策点策略契约：8 个 Policy Protocol + 共享类型。

逐字引用 contracts/policies.md（PRD V2.3 2.2 / 4.1.9）：
- 页级决策（#4–#8）参与 STP 计算，**必须规则、必须可复现**，任何情况下不得替换为模型。
- 任务级决策（#1–#3）每任务只发生几次，可由模型实现，但沿冻结 Plan 重放仍逐字一致。
- 所有 Policy **必须无状态**（实例不得持有跨调用状态），入参 / 出参可序列化。

契约层叶子模块（仅依赖 .defects）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Any, Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from dataagent.contracts.defects import Defect, OperatorResult, RepairOperator
from dataagent.contracts.errors import DefectCode

# 文本层恢复标记：PageProfile.class.route_reason 携带，档位策略据此豁免 T3
TEXT_LAYER_RECOVERED_REASON: Final[str] = "text_layer_recovered"

# 算子封闭集来自 defects（与 pipeline/repair/mapping.py 同源，均派生自 DEFECT_SPECS）
OperatorId = RepairOperator


@unique
class Tier(StrEnum):
    """兜底档位（PRD 4.3.5）：原样采用 / 后处理 / 参数重跑 / 换引擎。"""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


@unique
class Strategy(StrEnum):
    """整本策略切换（PRD 2.2）：整本 OCR / 降档 / 中止。"""

    WHOLE_BOOK_OCR = "whole_book_ocr"
    DOWNGRADE = "downgrade"
    ABORT = "abort"


@unique
class ChainId(StrEnum):
    """链路注册表 ID（PRD 4.2.2）。"""

    REJECT = "reject"
    TABULAR = "tabular"
    TEXT = "text"
    IMAGE = "image"
    PDF_NATIVE = "pdf.native"
    PDF_OCR = "pdf.ocr"
    PDF_VLM = "pdf.vlm"


@unique
class PdfType(StrEnum):
    TEXT_BASED = "text_based"
    SCANNED = "scanned"
    IMAGE_BASED = "image_based"
    MIXED = "mixed"


@unique
class RouteSuggestion(StrEnum):
    NATIVE = "native"
    OCR = "ocr"
    VLM = "vlm"


@unique
class StageKind(StrEnum):
    """Plan 只允许 3 种 stage kind（禁止自由图结构）。"""

    PROCESSING = "processing"
    DECISION = "decision"
    REPLAN = "replan"


@unique
class AttributionLabel(StrEnum):
    """失败归因封闭集（PRD 4.5.2：技术类 + 业务类）。"""

    ENCODING_BROKEN = "encoding_broken"
    TABLE_BREAK = "table_break"
    READING_ORDER = "reading_order"
    # 与缺陷码同名：引用枚举值，避免缺陷码字符串字面量外散（G9 门禁）
    LOW_DENSITY = DefectCode.LOW_DENSITY.value
    ROTATION = "rotation"
    SCAN_NOISE = "scan_noise"
    FIELD_MISSING = "field_missing"
    VALUE_MISMATCH = "value_mismatch"
    FORMAT_INVALID = "format_invalid"
    RULE_CONFLICT = "rule_conflict"


class ProfileClass(BaseModel):
    """PageProfile.class（路由阶段由 pdf.classify 写入）。"""

    model_config = ConfigDict(extra="forbid")

    pdf_type: PdfType
    class_confidence: float = Field(ge=0.0, le=1.0)
    route: RouteSuggestion
    route_reason: list[str] = Field(default_factory=list)


class ProfileLayout(BaseModel):
    """PageProfile.layout（路由阶段写入）。"""

    model_config = ConfigDict(extra="forbid")

    columns: int = Field(default=1, ge=1)
    has_table: bool = False
    is_complex: bool = False
    rotation: int = 0


class ProfileSignals(BaseModel):
    """PageProfile.signals（抽取后写入）。"""

    model_config = ConfigDict(extra="forbid")

    char_density: float = Field(ge=0.0)
    glyph_coverage: float = Field(ge=0.0, le=1.0)
    empty_region_ratio: float = Field(ge=0.0, le=1.0)
    header_footer_ratio: float = Field(ge=0.0, le=1.0)


class ProfileQuality(BaseModel):
    """PageProfile.quality（抽取后写入）。"""

    model_config = ConfigDict(extra="forbid")

    extract_quality: float = Field(ge=0.0, le=1.0)
    tier: Tier | None = None


class PageProfile(BaseModel):
    """页面画像（PRD 4.3.5）：分两次写入，signals / quality 抽取后才有。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    doc_id: str
    page_no: int = Field(ge=1, description="内部页码，一律 1-indexed")
    class_: ProfileClass = Field(alias="class")
    layout: ProfileLayout = Field(default_factory=ProfileLayout)
    signals: ProfileSignals | None = None
    quality: ProfileQuality | None = None

    @property
    def pdf_type(self) -> PdfType:
        return self.class_.pdf_type

    @property
    def class_confidence(self) -> float:
        return self.class_.class_confidence

    @property
    def route(self) -> RouteSuggestion:
        return self.class_.route

    @property
    def columns(self) -> int:
        return self.layout.columns

    @property
    def has_table(self) -> bool:
        return self.layout.has_table

    @property
    def is_complex(self) -> bool:
        return self.layout.is_complex

    @property
    def rotation(self) -> int:
        return self.layout.rotation

    @property
    def glyph_coverage(self) -> float | None:
        return self.signals.glyph_coverage if self.signals is not None else None

    @property
    def header_footer_ratio(self) -> float | None:
        return (
            self.signals.header_footer_ratio if self.signals is not None else None
        )


@dataclass(frozen=True, slots=True)
class PlanStage:
    id: str
    kind: StageKind
    scope: str | None = None
    strategy: Strategy | str | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    """任务级执行计划（data-model.md Plan）：冻结后不可改，重放逐字一致。"""

    plan_version: int
    stages: tuple[PlanStage, ...]
    created_by: str = "rule_template"


@dataclass(frozen=True, slots=True)
class Attribution:
    """失败归因：封闭 label + 置信度，禁止自由文本。"""

    label: AttributionLabel
    confidence: float


@runtime_checkable
class Budget(Protocol):
    """预算视图（kernel.budget.BudgetTracker 结构化满足）。"""

    @property
    def exhausted(self) -> bool: ...


@runtime_checkable
class PlanPolicy(Protocol):
    def compile(self, nl: str, task_stats: dict[str, Any]) -> Plan: ...


@runtime_checkable
class ReplanPolicy(Protocol):
    def decide(self, exec_stats: dict[str, Any]) -> Plan | None: ...


@runtime_checkable
class StrategyPolicy(Protocol):
    def select(self, task_stats: dict[str, Any]) -> Strategy: ...


@runtime_checkable
class RoutePolicy(Protocol):
    def route(self, profile: PageProfile) -> ChainId: ...


@runtime_checkable
class TierPolicy(Protocol):
    def tier(self, profile: PageProfile, quality: float) -> Tier: ...


@runtime_checkable
class OperatorPolicy(Protocol):
    def operator(self, defect: Defect) -> OperatorId: ...


@runtime_checkable
class AttemptPolicy(Protocol):
    def continue_(self, result: OperatorResult) -> bool: ...


@runtime_checkable
class EscalationPolicy(Protocol):
    def escalate(self, budget: Budget, attribution: Attribution) -> bool: ...
