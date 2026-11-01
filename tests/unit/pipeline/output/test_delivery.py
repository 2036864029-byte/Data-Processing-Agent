"""分级交付测试（T109，B7）——FR-018 / PRD 4.7.4。

gap 页占比 0% / 3% / 8% → complete / partial_with_gaps / needs_review；
needs_review 强制进人工队列；缺口标记内嵌产物、缺口页保留占位。
"""

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.merge.merger import merge_document
from dataagent.pipeline.output.delivery import DeliveryLevel, decide_delivery
from dataagent.pipeline.repair.fallback import FallbackPath
from dataagent.pipeline.repair.gap import PageGap

DOC_ID = "b" * 64


def artifact(page_no: int) -> PageArtifact:
    return PageArtifact(
        doc_id=DOC_ID,
        page_no=page_no,
        engine="engine.pdf@1.0.0",
        params_hash="params",
        markdown=f"page {page_no}\n",
    )


def gap(page_no: int) -> PageGap:
    return PageGap(
        doc_id=DOC_ID,
        page_no=page_no,
        defect_codes=(DefectCode.TABLE_STRUCTURE_BROKEN,),
        fallback_used=FallbackPath.MARK_ONLY,
    )


def document(total: int, gap_pages: list[int]):
    gaps = set(gap_pages)
    artifacts = [artifact(page) for page in range(1, total + 1) if page not in gaps]
    return merge_document(DOC_ID, artifacts, [gap(page) for page in gap_pages])


def test_complete_delivery_without_gaps() -> None:
    decision = decide_delivery(document(10, []))

    assert decision.level is DeliveryLevel.COMPLETE
    assert decision.requires_manual_review is False
    assert decision.gap_ratio == 0.0


def test_partial_with_gaps_at_three_percent() -> None:
    decision = decide_delivery(document(100, [10, 20, 30]))

    assert decision.level is DeliveryLevel.PARTIAL_WITH_GAPS
    assert decision.requires_manual_review is False
    assert 0.0 < decision.gap_ratio <= 0.05


def test_needs_review_above_tolerance() -> None:
    decision = decide_delivery(document(100, list(range(1, 9))))

    assert decision.level is DeliveryLevel.NEEDS_REVIEW
    assert decision.requires_manual_review is True


def test_gap_tolerance_boundary_is_inclusive() -> None:
    decision = decide_delivery(document(100, list(range(1, 6))))

    assert decision.level is DeliveryLevel.PARTIAL_WITH_GAPS
    assert decision.gap_ratio == 0.05


def test_manual_exception_forces_needs_review() -> None:
    """L4 人工例外经 needs_review 强制进人工队列。"""
    decision = decide_delivery(document(10, []), manual_exception=True)

    assert decision.level is DeliveryLevel.NEEDS_REVIEW
    assert decision.requires_manual_review is True


def test_gap_markers_are_embedded_in_output() -> None:
    merged = document(10, [4])

    assert merged.pages[3].gap is True
    assert "dataagent:gap" in merged.pages[3].markdown
    assert all(not page.gap for page in merged.pages if page.page_no != 4)


def test_gap_tolerance_is_configurable_but_never_drops_pages() -> None:
    merged = document(100, [10, 20, 30])

    decision = decide_delivery(merged, gap_tolerance=0.01)

    assert decision.level is DeliveryLevel.NEEDS_REVIEW
    assert len(merged.pages) == 100
