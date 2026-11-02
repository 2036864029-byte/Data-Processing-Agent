"""任务编排的数据模型与对外结果契约。

只放纯数据定义，不包含执行逻辑；编排器与阶段服务共享这些模型，
避免在单个大文件里混杂控制流。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from dataagent.contracts.errors import DefectCode
from dataagent.kernel.plan import DecisionRecord, FrozenPlan, PlanResolution
from dataagent.pipeline.ingest.ingestor import PendingRecord
from dataagent.pipeline.output.delivery import DeliveryLevel
from dataagent.pipeline.output.reports import ProcessReport
from dataagent.pipeline.quality.l2_sample import ProfileSpeed
from dataagent.pipeline.repair.fallback import (
    EnhancerKind,
    FallbackConfig,
    FallbackPath,
)

DEFAULT_BATCH_PAGES: Final[int] = 32
DEFAULT_TEXT_AREA_CM2: Final[float] = 468.0
DEFAULT_FALLBACK: Final[FallbackConfig] = FallbackConfig()


@dataclass(frozen=True, slots=True)
class TaskConfig:
    output_formats: tuple[str, ...] = ("json", "markdown")
    batch_pages: int = DEFAULT_BATCH_PAGES
    text_area_cm2: float = DEFAULT_TEXT_AREA_CM2
    gap_tolerance: float = 0.05
    fallback: FallbackConfig = DEFAULT_FALLBACK
    available_enhancers: frozenset[EnhancerKind] = frozenset()
    profile_speed: ProfileSpeed = ProfileSpeed.BALANCED
    plan: FrozenPlan | None = None


@dataclass(frozen=True, slots=True)
class TaskStats:
    pages_total: int = 0
    pages_processed: int = 0
    pages_failed: int = 0
    stp_rate: float = 0.0
    gap_ratio: float = 0.0
    avg_extract_quality: float = 0.0
    duration_s: float = 0.0
    model_calls: int = 0
    stopped_by: str | None = None

    def as_plan_stats(self) -> dict[str, float | int]:
        """映射到 Plan 分支白名单指标（PLAN_METRICS 子集）。"""
        return {
            "stp_rate": self.stp_rate,
            "pages_total": self.pages_total,
            "pages_processed": self.pages_processed,
            "pages_failed": self.pages_failed,
            "gap_ratio": self.gap_ratio,
            "avg_extract_quality": self.avg_extract_quality,
            "elapsed_s": self.duration_s,
            "model_calls": self.model_calls,
            "cost_per_page": 0.0,
        }


@dataclass(frozen=True, slots=True)
class DocumentOutcome:
    doc_id: str
    path: str
    family: str
    status: str = "processed"
    pages: int = 0
    gap_pages: int = 0
    level: DeliveryLevel | None = None
    defect_codes: tuple[DefectCode, ...] = ()
    outputs: tuple[str, ...] = ()
    fallback_path: FallbackPath | None = None
    note: str = ""
    avg_extract_quality: float = 0.0
    failure_counts: tuple[tuple[DefectCode, int], ...] = ()


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    documents: tuple[DocumentOutcome, ...]
    report: ProcessReport
    stats: TaskStats
    pending: tuple[PendingRecord, ...] = ()
    plan_resolution: PlanResolution | None = None
    decisions: tuple[DecisionRecord, ...] = ()
    machine_state: str | None = None

    def count(self, status: str) -> int:
        return sum(1 for document in self.documents if document.status == status)
