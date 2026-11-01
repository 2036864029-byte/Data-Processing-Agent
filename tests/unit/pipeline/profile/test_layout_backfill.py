"""画像/路由恢复测试（T132，Phase 12 R2）。

- 抽取后回填 layout（表格/分栏/旋转/复杂度），不改路由段；
- 隐藏文本层恢复页 route=native、route_reason 追加 `text_layer_recovered`；
- 恢复幂等，且不丢已写入的质量段。
"""

import pytest

from dataagent.contracts.policies import (
    ChainId,
    PageProfile,
    PdfType,
    ProfileClass,
    ProfileLayout,
    ProfileQuality,
    ProfileSignals,
    RouteSuggestion,
    Tier,
)
from dataagent.pipeline.profile.writer import (
    InMemoryProfileStore,
    PageProfileWriter,
    ProfileStageOrderError,
)
from dataagent.pipeline.route.routes import RouteDecision, RouteLevel

DOC_ID = "a" * 64
PAGE_NO = 3


def route_decision() -> RouteDecision:
    profile = PageProfile.model_validate(
        {
            "doc_id": DOC_ID,
            "page_no": PAGE_NO,
            "class": ProfileClass(
                pdf_type=PdfType.IMAGE_BASED,
                class_confidence=0.70,
                route=RouteSuggestion.OCR,
                route_reason=["invisible_text_layer"],
            ),
            "layout": ProfileLayout(),
        }
    )
    return RouteDecision(
        doc_id=DOC_ID,
        level=RouteLevel.PAGE,
        page_no=PAGE_NO,
        chain=ChainId.PDF_OCR,
        reasons=("invisible_text_layer",),
        profile_snapshot=profile.model_dump(by_alias=True, mode="json"),
    )


def writer() -> PageProfileWriter:
    return PageProfileWriter(InMemoryProfileStore())


def test_extraction_layout_backfills_without_touching_route() -> None:
    w = writer()
    w.write_route(route_decision())

    profile = w.write_extraction_layout(
        DOC_ID,
        PAGE_NO,
        has_table=True,
        columns=2,
        is_complex=True,
        rotation=90,
    )

    assert profile.layout == ProfileLayout(
        columns=2, has_table=True, is_complex=True, rotation=90
    )
    assert profile.route is RouteSuggestion.OCR
    assert profile.class_.route_reason == ["invisible_text_layer"]


def test_text_layer_recovery_sets_native_route() -> None:
    w = writer()
    w.write_route(route_decision())

    profile = w.apply_text_layer_recovery(DOC_ID, PAGE_NO)

    assert profile.route is RouteSuggestion.NATIVE
    assert "text_layer_recovered" in profile.class_.route_reason
    assert "invisible_text_layer" in profile.class_.route_reason
    assert profile.class_.pdf_type is PdfType.IMAGE_BASED
    assert profile.class_.class_confidence == 0.70


def test_text_layer_recovery_is_idempotent() -> None:
    w = writer()
    w.write_route(route_decision())

    w.apply_text_layer_recovery(DOC_ID, PAGE_NO)
    profile = w.apply_text_layer_recovery(DOC_ID, PAGE_NO)

    assert profile.class_.route_reason.count("text_layer_recovered") == 1


def test_recovery_preserves_layout_and_quality() -> None:
    w = writer()
    w.write_route(route_decision())
    w.write_extraction_layout(
        DOC_ID, PAGE_NO, has_table=True, columns=2, is_complex=True
    )
    signals = ProfileSignals(
        char_density=12.0,
        glyph_coverage=0.99,
        empty_region_ratio=0.05,
        header_footer_ratio=0.01,
    )
    w.write_quality(
        DOC_ID,
        PAGE_NO,
        signals=signals,
        quality=ProfileQuality(extract_quality=0.92, tier=Tier.T1),
    )

    profile = w.apply_text_layer_recovery(DOC_ID, PAGE_NO)

    assert profile.layout.has_table is True
    assert profile.layout.columns == 2
    assert profile.signals == signals
    assert profile.quality is not None
    assert profile.quality.extract_quality == 0.92


def test_layout_backfill_requires_route_segment() -> None:
    with pytest.raises(ProfileStageOrderError):
        writer().write_extraction_layout(
            DOC_ID, PAGE_NO, has_table=True, columns=1, is_complex=False
        )


def test_recovery_requires_route_segment() -> None:
    with pytest.raises(ProfileStageOrderError):
        writer().apply_text_layer_recovery(DOC_ID, PAGE_NO)
