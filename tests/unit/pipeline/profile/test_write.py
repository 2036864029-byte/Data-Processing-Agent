"""页面画像两段写入测试（T090，B4）。

路由段（class / layout）先写；解析完成后写 signals / quality。
`glyph_coverage` 来自 cmap_gaps，仅抽取后可得——路由阶段恒为空。
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
ROUTE_REASON = ("low_char_density", "no_text_layer")


def route_decision(
    *,
    page_no: int = PAGE_NO,
    route_reason: tuple[str, ...] = ROUTE_REASON,
    rotation: int = 90,
    level: RouteLevel = RouteLevel.PAGE,
) -> RouteDecision:
    profile = PageProfile.model_validate(
        {
            "doc_id": DOC_ID,
            "page_no": page_no,
            "class": ProfileClass(
                pdf_type=PdfType.TEXT_BASED,
                class_confidence=0.91,
                route=RouteSuggestion.OCR,
                route_reason=list(route_reason),
            ),
            "layout": ProfileLayout(
                columns=2, has_table=True, is_complex=True, rotation=rotation
            ),
        }
    )
    return RouteDecision(
        doc_id=DOC_ID,
        level=level,
        page_no=None if level is RouteLevel.FORMAT else page_no,
        chain=ChainId.PDF_OCR,
        reasons=route_reason,
        profile_snapshot=profile.model_dump(by_alias=True, mode="json"),
    )


def writer() -> PageProfileWriter:
    return PageProfileWriter(InMemoryProfileStore())


def test_route_segment_written_first() -> None:
    """路由段先写：class / layout 全部字段 + route_reason 原样。"""
    w = writer()

    profile = w.write_route(route_decision())

    assert profile.pdf_type is PdfType.TEXT_BASED
    assert profile.class_confidence == 0.91
    assert profile.route is RouteSuggestion.OCR
    assert profile.class_.route_reason == list(ROUTE_REASON)
    assert profile.columns == 2
    assert profile.has_table is True
    assert profile.is_complex is True
    assert profile.rotation == 90
    assert profile.signals is None
    assert profile.quality is None


def test_quality_segment_requires_route_segment() -> None:
    """两段写入有序：路由段未写时禁止写质量段。"""
    w = writer()

    with pytest.raises(ProfileStageOrderError):
        w.write_quality(
            DOC_ID,
            1,
            signals=ProfileSignals(
                char_density=10.0,
                glyph_coverage=0.99,
                empty_region_ratio=0.1,
                header_footer_ratio=0.02,
            ),
            quality=ProfileQuality(extract_quality=0.9, tier=Tier.T1),
        )


def test_quality_segment_written_after_extraction() -> None:
    """解析完成后写 signals / quality；路由段保持不变。"""
    w = writer()
    w.write_route(route_decision())

    signals = ProfileSignals(
        char_density=12.5,
        glyph_coverage=0.97,
        empty_region_ratio=0.2,
        header_footer_ratio=0.03,
    )
    quality = ProfileQuality(extract_quality=0.88, tier=Tier.T1)

    updated = w.write_quality(DOC_ID, PAGE_NO, signals=signals, quality=quality)

    assert updated.signals == signals
    assert updated.quality == quality
    assert updated.class_.route_reason == list(ROUTE_REASON)
    assert updated.columns == 2
    assert updated.has_table is True
    assert updated.rotation == 90
    assert w.get(DOC_ID, PAGE_NO) == updated


def test_glyph_coverage_only_available_after_extraction() -> None:
    """glyph_coverage 来自 cmap_gaps，仅抽取后可得（路由阶段为 None）。"""
    w = writer()
    profile = w.write_route(route_decision())

    assert profile.glyph_coverage is None

    updated = w.write_quality(
        DOC_ID,
        PAGE_NO,
        signals=ProfileSignals(
            char_density=12.5,
            glyph_coverage=0.97,
            empty_region_ratio=0.2,
            header_footer_ratio=0.03,
        ),
        quality=ProfileQuality(extract_quality=0.88, tier=Tier.T1),
    )

    assert updated.glyph_coverage == 0.97
    assert updated.header_footer_ratio == 0.03


def test_route_rewrite_keeps_quality_segment() -> None:
    """路由段重写幂等，且不清空已写入的质量段（批级重试安全）。"""
    w = writer()
    w.write_route(route_decision())
    signals = ProfileSignals(
        char_density=12.5,
        glyph_coverage=0.97,
        empty_region_ratio=0.2,
        header_footer_ratio=0.03,
    )
    quality = ProfileQuality(extract_quality=0.88, tier=Tier.T1)
    w.write_quality(DOC_ID, PAGE_NO, signals=signals, quality=quality)

    again = w.write_route(route_decision())

    assert again.signals == signals
    assert again.quality == quality


def test_quality_rewrite_replaces_previous_quality() -> None:
    w = writer()
    w.write_route(route_decision())
    first = ProfileSignals(
        char_density=10.0,
        glyph_coverage=0.90,
        empty_region_ratio=0.3,
        header_footer_ratio=0.05,
    )
    w.write_quality(
        DOC_ID, PAGE_NO, signals=first, quality=ProfileQuality(extract_quality=0.8)
    )

    second = ProfileSignals(
        char_density=14.0,
        glyph_coverage=0.98,
        empty_region_ratio=0.1,
        header_footer_ratio=0.01,
    )
    updated = w.write_quality(
        DOC_ID,
        PAGE_NO,
        signals=second,
        quality=ProfileQuality(extract_quality=0.95, tier=Tier.T0),
    )

    assert updated.signals == second
    assert updated.quality is not None
    assert updated.quality.extract_quality == 0.95
    assert updated.quality.tier is Tier.T0


def test_format_decision_cannot_write_route() -> None:
    """一级格式决策没有页级画像段，不得写 PageProfile。"""
    w = writer()

    with pytest.raises(ValueError):
        w.write_route(route_decision(level=RouteLevel.FORMAT))
