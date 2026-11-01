"""文本层恢复页的档位例外测试（T132，Phase 12 R2）。

恢复页 route=native、route_reason 含 `text_layer_recovered` 时，
不再因 pdf_type=image_based / 分类置信度低而强制 T3；质量与版式照常判档。
"""

from dataagent.contracts.policies import (
    PageProfile,
    PdfType,
    ProfileClass,
    ProfileLayout,
    ProfileSignals,
    RouteSuggestion,
    Tier,
)
from dataagent.kernel.policies.rule_tier import RuleTierPolicy


def profile(*, recovered: bool, glyph_coverage: float | None = None) -> PageProfile:
    reasons = ["text_layer_recovered"] if recovered else ["invisible_text_layer"]
    values = {
        "doc_id": "a" * 64,
        "page_no": 1,
        "class": ProfileClass(
            pdf_type=PdfType.IMAGE_BASED,
            class_confidence=0.70,
            route=RouteSuggestion.NATIVE,
            route_reason=reasons,
        ),
        "layout": ProfileLayout(),
    }
    if glyph_coverage is not None:
        values["signals"] = ProfileSignals(
            char_density=10.0,
            glyph_coverage=glyph_coverage,
            empty_region_ratio=0.05,
            header_footer_ratio=0.01,
        )
    return PageProfile.model_validate(values)


def test_recovered_page_is_not_forced_to_t3() -> None:
    tier = RuleTierPolicy().tier(profile(recovered=True), quality=0.92)

    assert tier is not Tier.T3


def test_unrecovered_image_page_is_still_t3() -> None:
    tier = RuleTierPolicy().tier(profile(recovered=False), quality=0.92)

    assert tier is Tier.T3


def test_recovered_page_with_low_glyph_coverage_is_still_t3() -> None:
    tier = RuleTierPolicy().tier(
        profile(recovered=True, glyph_coverage=0.50), quality=0.92
    )

    assert tier is Tier.T3
