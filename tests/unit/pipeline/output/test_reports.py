"""分层报告测试（T110，B7）——FR-019 / PRD 4.7.5。

总览一页 + 按原因聚合 ≤ 10 行 + 文件清单 + 按需明细；
默认只输出"有缺口的文件"明细，不预生成巨型页级明细。
"""

from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.output.delivery import DeliveryLevel
from dataagent.pipeline.output.reports import (
    DocSummary,
    PageDetail,
    build_report,
)
from dataagent.pipeline.repair.fallback import FallbackPath


def clean_summary(index: int) -> DocSummary:
    return DocSummary(
        doc_id=f"clean-{index:03d}",
        pages=10,
        gap_pages=0,
        level=DeliveryLevel.COMPLETE,
        cost=0.01,
    )


def gap_summary(index: int, code: DefectCode) -> DocSummary:
    return DocSummary(
        doc_id=f"gap-{index:03d}",
        pages=10,
        gap_pages=2,
        level=DeliveryLevel.PARTIAL_WITH_GAPS,
        cost=0.02,
        failure_counts=((code, 2),),
    )


def summaries() -> list[DocSummary]:
    result = [clean_summary(index) for index in range(95)]
    codes = [
        DefectCode.ENCODING_LOSS,
        DefectCode.TABLE_STRUCTURE_BROKEN,
        DefectCode.HEADER_FOOTER_LEAK,
        DefectCode.READING_ORDER_BROKEN,
        DefectCode.REGION_EMPTY,
    ]
    result.extend(gap_summary(index, codes[index % len(codes)]) for index in range(5))
    result.extend(
        gap_summary(100 + index, code)
        for index, code in enumerate(
            list(DefectCode)[:10]
        )
    )
    return result


def details_for_gap_doc(doc_id: str) -> list[PageDetail]:
    return [
        PageDetail(
            doc_id=doc_id,
            page_no=3,
            extract_quality=0.4,
            defect_codes=(DefectCode.TABLE_STRUCTURE_BROKEN,),
            fallback_used=FallbackPath.MARK_ONLY,
        )
    ]


def test_overview_contains_core_numbers() -> None:
    report = build_report(summaries(), duration_s=12.5, cost_total=1.23)

    overview = report.overview
    assert overview.total_docs == len(summaries())
    assert overview.docs_with_gaps == 15
    assert overview.gap_pages == 30
    assert overview.total_pages == len(summaries()) * 10
    assert overview.duration_s == 12.5
    assert overview.cost_total == 1.23
    assert overview.top_failure is not None


def test_by_reason_is_capped_at_ten_rows() -> None:
    report = build_report(summaries(), duration_s=1.0, cost_total=1.0)

    assert len(report.by_reason) == 10
    page_counts = [row.page_count for row in report.by_reason]
    assert page_counts == sorted(page_counts, reverse=True)


def test_files_layer_only_lists_documents_with_gaps() -> None:
    report = build_report(summaries(), duration_s=1.0, cost_total=1.0)

    assert len(report.files) == 15
    assert all(row.gap_pages > 0 for row in report.files)
    assert all(not row.doc_id.startswith("clean-") for row in report.files)


def test_details_are_lazy_and_only_for_gap_documents() -> None:
    report = build_report(
        summaries(),
        duration_s=1.0,
        cost_total=1.0,
        page_details={
            "gap-000": details_for_gap_doc("gap-000"),
            "clean-000": details_for_gap_doc("clean-000"),
        },
    )

    assert report.detail("gap-000")
    assert report.detail("clean-000") == ()
    assert report.detail("gap-001") == ()


def test_report_dict_does_not_pre_generate_page_details() -> None:
    report = build_report(
        summaries(),
        duration_s=1.0,
        cost_total=1.0,
        page_details={"gap-000": details_for_gap_doc("gap-000")},
    )

    payload = report.as_dict()

    assert set(payload) == {"overview", "by_reason", "files"}


def test_markdown_report_stays_small() -> None:
    report = build_report(summaries(), duration_s=1.0, cost_total=1.0)

    text = report.to_markdown()

    assert "# 处理报告" in text
    assert "## 任务总览" in text
    assert "## 按原因聚合" in text
    assert "## 文件清单" in text
    assert len(text.splitlines()) < 60
