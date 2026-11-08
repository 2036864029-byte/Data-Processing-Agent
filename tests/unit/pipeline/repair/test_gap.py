"""PageGap 记录测试：扫描页缺口必须留下 needs_ocr 标识（Phase 4.2）。"""

from dataagent.contracts.artifacts import PageArtifact
from dataagent.pipeline.repair.fallback import FallbackPath
from dataagent.pipeline.repair.gap import page_gap_from_outcome
from dataagent.pipeline.repair.loop import EnhancementOutcome


def artifact(*, needs_ocr: bool) -> PageArtifact:
    return PageArtifact(
        doc_id="a" * 64,
        page_no=3,
        engine="engine.pdf@1.0.0",
        params_hash="p",
        markdown="",
        needs_ocr=needs_ocr,
    )


def outcome(value: PageArtifact) -> EnhancementOutcome:
    return EnhancementOutcome(
        artifact=value,
        remaining_defects=(),
        actions=(),
        escalated=False,
        escalation_reason=None,
        cost=0,
    )


def test_gap_marks_needs_ocr_pages() -> None:
    value = artifact(needs_ocr=True)

    gap = page_gap_from_outcome(
        value, outcome(value), fallback=FallbackPath.MARK_ONLY
    )

    assert "needs_ocr" in gap.evidence


def test_gap_without_needs_ocr_has_no_marker() -> None:
    value = artifact(needs_ocr=False)

    gap = page_gap_from_outcome(
        value, outcome(value), fallback=FallbackPath.MARK_ONLY
    )

    assert "needs_ocr" not in gap.evidence
