"""route_reason 原样保留测试（T091，B4）——G2。

上游返回的路由原因原样落库：不裁剪 / 不映射 / 不丢弃；
映射成缺陷码发生在配置表而非落库时；page_filter 禁用 extract_quality（因果循环）。
"""

import pytest

from dataagent.contracts.policies import (
    ChainId,
    PageProfile,
    PdfType,
    ProfileClass,
    ProfileLayout,
    RouteSuggestion,
)
from dataagent.pipeline.profile.filters import (
    PAGE_FILTER_ALLOWED_GROUPS,
    validate_page_filter_fields,
)
from dataagent.pipeline.profile.writer import InMemoryProfileStore, PageProfileWriter
from dataagent.pipeline.route.routes import RouteDecision, RouteLevel

DOC_ID = "b" * 64


def write_with_reasons(reasons: list[str]) -> PageProfile:
    profile = PageProfile.model_validate(
        {
            "doc_id": DOC_ID,
            "page_no": 7,
            "class": ProfileClass(
                pdf_type=PdfType.SCANNED,
                class_confidence=0.72,
                route=RouteSuggestion.OCR,
                route_reason=reasons,
            ),
            "layout": ProfileLayout(),
        }
    )
    decision = RouteDecision(
        doc_id=DOC_ID,
        level=RouteLevel.PAGE,
        page_no=7,
        chain=ChainId.PDF_OCR,
        reasons=tuple(reasons),
        profile_snapshot=profile.model_dump(by_alias=True, mode="json"),
    )
    return PageProfileWriter(InMemoryProfileStore()).write_route(decision)


def test_route_reason_stored_verbatim() -> None:
    """未知 / 罕见原因原样保留，不裁剪、不归一化、不丢弃。"""
    reasons = ["Weird_Plugin_Reason", "low_char_density", "x"]

    profile = write_with_reasons(reasons)

    assert profile.class_.route_reason == reasons


def test_route_reason_is_not_mapped_to_defect_code() -> None:
    """落库只保留原始原因；缺陷码映射属配置表，不在写入时发生。"""
    reasons = [f"reason_{index}" for index in range(30)]

    profile = write_with_reasons(reasons)

    assert profile.class_.route_reason == reasons
    dumped = profile.model_dump(by_alias=True)
    assert "defect_code" not in dumped
    assert "defect_code" not in dumped["class"]


def test_duplicate_and_empty_reasons_preserved() -> None:
    """重复项与空列表都原样保留（不排序、不去重）。"""
    assert write_with_reasons([]).class_.route_reason == []
    assert write_with_reasons(["a", "a", "b"]).class_.route_reason == ["a", "a", "b"]


def test_page_filter_whitelist_groups() -> None:
    assert {"class", "layout", "signals"} == PAGE_FILTER_ALLOWED_GROUPS
    validate_page_filter_fields(
        ["class.pdf_type", "class.route", "layout.has_table", "signals.char_density"]
    )


@pytest.mark.parametrize(
    "field",
    [
        "extract_quality",
        "quality.extract_quality",
        "quality.tier",
        "signals.extract_quality",
    ],
)
def test_page_filter_rejects_extract_quality(field: str) -> None:
    """因果循环：质量分是解析后的产物，不能决定哪些页被处理。"""
    with pytest.raises(ValueError):
        validate_page_filter_fields([field])


def test_page_filter_rejects_unknown_group() -> None:
    with pytest.raises(ValueError):
        validate_page_filter_fields(["page.number"])
