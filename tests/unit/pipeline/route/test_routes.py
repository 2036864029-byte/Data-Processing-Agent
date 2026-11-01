"""两级路由测试（T083，B3）——FR-004。

一级 = 边界与格式判定；二级仅在 PDF 家族内调用 pdf.classify 做页级路由。
非 PDF 家族不得经过 PDF 分类能力；每条决策带理由与决策时画像快照并落库。
"""

from pathlib import Path

import pytest

from dataagent.contracts.artifacts import PageOcrReason, PdfClassification
from dataagent.contracts.policies import ChainId, PdfType
from dataagent.kernel.policies.rule_route import RuleRoutePolicy
from dataagent.pipeline.route.routes import (
    InMemoryRouteDecisionStore,
    RouteLevel,
    TwoLevelRouter,
)
from dataagent.pipeline.sniff.sniffer import Family, SniffResult, SniffState


class FakePdfClassifier:
    def __init__(self, classification: PdfClassification) -> None:
        self.classification = classification
        self.calls: list[Path] = []

    def classify(self, path: Path) -> PdfClassification:
        self.calls.append(Path(path))
        return self.classification


def pdf_sniff(**overrides: object) -> SniffResult:
    values: dict[str, object] = {
        "family": Family.PDF,
        "state": SniffState.CONFIDENT,
        "reasons": ("pdf_magic",),
    }
    values.update(overrides)
    return SniffResult(**values)  # type: ignore[arg-type]


def classification(**overrides: object) -> PdfClassification:
    values: dict[str, object] = {
        "pdf_type": PdfType.TEXT_BASED,
        "confidence": 0.93,
        "page_count": 3,
        "pages_needing_ocr": (2,),
        "ocr_reasons_by_page": (
            PageOcrReason(page=2, reasons=("low_char_density", "no_text_layer")),
        ),
        "pages_with_tables": (3,),
    }
    values.update(overrides)
    return PdfClassification(**values)  # type: ignore[arg-type]


def router_for(
    classifier: FakePdfClassifier,
) -> tuple[TwoLevelRouter, InMemoryRouteDecisionStore]:
    store = InMemoryRouteDecisionStore()
    router = TwoLevelRouter(
        decisions=store,
        policy=RuleRoutePolicy(),
        classifier=classifier,
    )
    return router, store


def test_pdf_first_level_defers_to_second_level() -> None:
    """PDF 一级只判定家族；页级路由由二级分类完成，非 PDF 不进入二级。"""
    classifier = FakePdfClassifier(classification())
    router, store = router_for(classifier)

    outcome = router.route(
        doc_id="a" * 64, path=Path("doc.pdf"), sniff=pdf_sniff()
    )

    assert outcome.family is Family.PDF
    assert outcome.level1.level is RouteLevel.FORMAT
    assert outcome.level1.chain is None
    assert "family:pdf" in outcome.level1.reasons
    assert "pdf_magic" in outcome.level1.reasons
    assert classifier.calls == [Path("doc.pdf")]
    assert len(outcome.page_decisions) == 3
    assert store.for_doc("a" * 64)[0] == outcome.level1


def test_pdf_pages_are_routed_from_classification() -> None:
    """页级链路来自 pdf.classify：OCR 页 → pdf.ocr，其余 → pdf.native。"""
    classifier = FakePdfClassifier(classification())
    router, _ = router_for(classifier)

    outcome = router.route(
        doc_id="b" * 64, path=Path("doc.pdf"), sniff=pdf_sniff()
    )
    chains = {decision.page_no: decision.chain for decision in outcome.page_decisions}

    assert chains == {
        1: ChainId.PDF_NATIVE,
        2: ChainId.PDF_OCR,
        3: ChainId.PDF_NATIVE,
    }


def test_route_reason_is_preserved_verbatim() -> None:
    """上游机器可读原因原样保留（G2）：不裁剪 / 不映射 / 不丢弃。"""
    classifier = FakePdfClassifier(classification())
    router, _ = router_for(classifier)

    outcome = router.route(
        doc_id="c" * 64, path=Path("doc.pdf"), sniff=pdf_sniff()
    )
    page_two = next(decision for decision in outcome.page_decisions if decision.page_no == 2)

    assert page_two.reasons[:2] == ("low_char_density", "no_text_layer")
    class_snapshot = page_two.profile_snapshot["class"]
    assert class_snapshot["route_reason"] == ["low_char_density", "no_text_layer"]
    assert class_snapshot["route"] == "ocr"


def test_decision_carries_profile_snapshot_for_explanation() -> None:
    """每条路由决策带决策时画像快照（FR-004），可事后解释。"""
    classifier = FakePdfClassifier(classification())
    router, _ = router_for(classifier)

    outcome = router.route(
        doc_id="d" * 64, path=Path("doc.pdf"), sniff=pdf_sniff()
    )
    page_three = next(
        decision for decision in outcome.page_decisions if decision.page_no == 3
    )

    assert page_three.profile_snapshot["page_no"] == 3
    assert page_three.profile_snapshot["class"]["pdf_type"] == "text_based"
    assert page_three.profile_snapshot["layout"]["has_table"] is True
    assert page_three.profile_snapshot["layout"]["columns"] == 1


@pytest.mark.parametrize(
    ("family", "chain"),
    [
        (Family.TABULAR, ChainId.TABULAR),
        (Family.TEXT, ChainId.TEXT),
        (Family.IMAGE, ChainId.IMAGE),
        (Family.UNKNOWN, ChainId.REJECT),
    ],
)
def test_non_pdf_never_invokes_pdf_classifier(
    family: Family, chain: ChainId
) -> None:
    """非 PDF 家族不得经过 PDF 分类能力（FR-004）。"""
    classifier = FakePdfClassifier(classification())
    router, store = router_for(classifier)

    outcome = router.route(
        doc_id="e" * 64,
        path=Path("other.bin"),
        sniff=SniffResult(family=family, state=SniffState.CONFIDENT, reasons=("magic",)),
    )

    assert classifier.calls == []
    assert outcome.level1.chain is chain
    assert outcome.page_decisions == ()
    assert store.for_doc("e" * 64) == [outcome.level1]
    assert f"family:{family.value}" in outcome.level1.reasons


def test_extension_mismatch_is_recorded_on_decision() -> None:
    """后缀不符按内容判定，事件写入理由而非猜测（B2 → B3 决策链）。"""
    classifier = FakePdfClassifier(classification())
    router, _ = router_for(classifier)

    outcome = router.route(
        doc_id="f" * 64,
        path=Path("renamed.pdf"),
        sniff=SniffResult(
            family=Family.TABULAR,
            state=SniffState.CONFIDENT,
            reasons=("delimiter:comma",),
            extension_mismatch=True,
        ),
    )

    assert outcome.level1.chain is ChainId.TABULAR
    assert "extension_mismatch" in outcome.level1.reasons


def test_unclassified_sniff_is_rejected_without_classifier() -> None:
    """无法分类只走一级拒绝，不进入 PDF 二级。"""
    classifier = FakePdfClassifier(classification())
    router, _ = router_for(classifier)

    outcome = router.route(
        doc_id="1" * 64,
        path=Path("unknown.bin"),
        sniff=SniffResult(
            family=Family.UNKNOWN,
            state=SniffState.UNCLASSIFIED,
            reasons=("no_magic",),
        ),
    )

    assert outcome.level1.chain is ChainId.REJECT
    assert classifier.calls == []


def test_decisions_are_persisted_in_document_order() -> None:
    """一级 + 全部页级决策按顺序落库，可还原完整决策链。"""
    classifier = FakePdfClassifier(classification())
    router, store = router_for(classifier)

    router.route(doc_id="2" * 64, path=Path("doc.pdf"), sniff=pdf_sniff())

    saved = store.for_doc("2" * 64)
    assert [decision.level for decision in saved] == [
        RouteLevel.FORMAT,
        RouteLevel.PAGE,
        RouteLevel.PAGE,
        RouteLevel.PAGE,
    ]
    assert [decision.page_no for decision in saved] == [None, 1, 2, 3]


def test_route_is_replayable_without_timestamps() -> None:
    """同一输入两次决策逐字一致（确定性，宪法 I）。"""
    first_router, first_store = router_for(FakePdfClassifier(classification()))
    second_router, second_store = router_for(FakePdfClassifier(classification()))

    first = first_router.route(doc_id="3" * 64, path=Path("doc.pdf"), sniff=pdf_sniff())
    second = second_router.route(doc_id="3" * 64, path=Path("doc.pdf"), sniff=pdf_sniff())

    assert [decision.as_dict() for decision in first_store.for_doc("3" * 64)] == [
        decision.as_dict() for decision in second_store.for_doc("3" * 64)
    ]
    assert first.page_decisions == second.page_decisions
