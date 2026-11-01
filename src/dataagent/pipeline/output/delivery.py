"""分级交付（B7/T112 / FR-018 / PRD 4.7.4）。

三档不是开关：complete / partial_with_gaps / needs_review。
缺口页占比超过 `gap_tolerance`（默认 5%）强制进人工队列；
`gap_tolerance` 可配置，但缺口页永远保留占位、不得删除。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final

from dataagent.pipeline.merge.merger import MergedDocument

DEFAULT_GAP_TOLERANCE: Final[float] = 0.05


@unique
class DeliveryLevel(StrEnum):
    COMPLETE = "complete"
    PARTIAL_WITH_GAPS = "partial_with_gaps"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    level: DeliveryLevel
    gap_ratio: float
    gap_pages: tuple[int, ...]
    requires_manual_review: bool
    report_note: str


def decide_delivery(
    document: MergedDocument,
    *,
    gap_tolerance: float = DEFAULT_GAP_TOLERANCE,
    manual_exception: bool = False,
) -> DeliveryDecision:
    if not 0.0 <= gap_tolerance <= 1.0:
        raise ValueError(f"gap_tolerance 必须在 [0, 1]，收到 {gap_tolerance}")

    total_pages = len(document.pages)
    gap_pages = document.gap_pages
    gap_ratio = len(gap_pages) / total_pages if total_pages else 0.0

    if manual_exception:
        level = DeliveryLevel.NEEDS_REVIEW
        note = "L4 人工例外：强制进入人工复核队列"
    elif not gap_pages:
        level = DeliveryLevel.COMPLETE
        note = ""
    elif gap_ratio <= gap_tolerance:
        level = DeliveryLevel.PARTIAL_WITH_GAPS
        note = (
            f"包含 {len(gap_pages)} 个缺口页（占比 {gap_ratio:.1%}），"
            "产物内已内嵌 gap 标记"
        )
    else:
        level = DeliveryLevel.NEEDS_REVIEW
        note = (
            f"缺口页占比 {gap_ratio:.1%} 超过容忍度 {gap_tolerance:.1%}，"
            "强制进入人工复核队列"
        )

    return DeliveryDecision(
        level=level,
        gap_ratio=gap_ratio,
        gap_pages=gap_pages,
        requires_manual_review=level is DeliveryLevel.NEEDS_REVIEW,
        report_note=note,
    )
