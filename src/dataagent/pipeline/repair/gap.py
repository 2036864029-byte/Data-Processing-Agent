"""PageGap：复杂页收口记录（B6/T107 / data-model.md PageGap）。

归复杂页后写一条 PageGap（精确到页），交聚包批量增强或人工；
之后不再对该页做任何动作。
"""

from __future__ import annotations

from dataclasses import dataclass

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.defects import RepairTier
from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.repair.fallback import FallbackPath
from dataagent.pipeline.repair.loop import EnhancementOutcome


@dataclass(frozen=True, slots=True)
class PageGap:
    doc_id: str
    page_no: int
    defect_codes: tuple[DefectCode, ...]
    tried_operators: tuple[str, ...] = ()
    recommended_tier: RepairTier | None = None
    fallback_used: FallbackPath = FallbackPath.MARK_ONLY
    evidence: tuple[str, ...] = ()


def page_gap_from_outcome(
    artifact: PageArtifact,
    outcome: EnhancementOutcome,
    *,
    fallback: FallbackPath,
    recommended_tier: RepairTier | None = None,
) -> PageGap:
    codes = tuple(dict.fromkeys(defect.code for defect in outcome.remaining_defects))
    tried = tuple(dict.fromkeys(action.operator.value for action in outcome.actions))
    notes = tuple(
        dict.fromkeys(action.notes for action in outcome.actions if action.notes)
    )
    return PageGap(
        doc_id=artifact.doc_id,
        page_no=artifact.page_no,
        defect_codes=codes,
        tried_operators=tried,
        recommended_tier=recommended_tier,
        fallback_used=fallback,
        evidence=notes,
    )
