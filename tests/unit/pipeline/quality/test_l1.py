"""L1 全量硬门控测试（T093，B5）——FR-009。

逐页确定性指标全量计算、硬门控、定位到页；任何性能档位下全量执行
（L1Checker 不接收性能档位参数，档位只影响 L2 抽样——见 test_l2.py）。
"""

import dataclasses

import pytest

from dataagent.contracts.errors import DefectCode
from dataagent.contracts.messages import Verdict
from dataagent.contracts.policies import PdfType
from dataagent.pipeline.quality.l1_check import (
    L1_CHECKER,
    L1Metrics,
    L1PageInput,
    defects_for_repair,
)

DOC_ID = "a" * 64


def healthy_metrics(**overrides: object) -> L1Metrics:
    values: dict[str, object] = {
        "char_density": 8.0,
        "glyph_coverage": 0.995,
        "cid_unmapped_ratio": 0.001,
        "empty_region_ratio": 0.05,
        "table_cell_fill": 0.98,
        "row_col_consistency": 0.0,
        "row_occupancy": 1.0,
        "col_occupancy": 1.0,
        "reading_order_score": 0.97,
        "bbox_coverage": 0.99,
        "header_footer_leak": 0.01,
        "page_number_ok": True,
    }
    values.update(overrides)
    return L1Metrics(**values)  # type: ignore[arg-type]


def page_input(**overrides: object) -> L1PageInput:
    values: dict[str, object] = {
        "doc_id": DOC_ID,
        "page_no": 5,
        "metrics": healthy_metrics(),
    }
    values.update(overrides)
    return L1PageInput(**values)  # type: ignore[arg-type]


def codes(page: L1PageInput) -> set[DefectCode]:
    return {defect.code for defect in L1_CHECKER.check(page).defects}


def test_healthy_page_passes() -> None:
    report = L1_CHECKER.check(page_input())

    assert report.verdict is Verdict.PASS
    assert report.defects == []
    assert report.extract_quality > 0.9
    assert report.qc_confidence == 1.0


def test_encoding_loss_fails_with_page_location() -> None:
    report = L1_CHECKER.check(
        page_input(metrics=healthy_metrics(glyph_coverage=0.80))
    )

    assert report.verdict is Verdict.FAIL
    defect = next(d for d in report.defects if d.code is DefectCode.ENCODING_LOSS)
    assert defect.location.page_no == 5
    assert defect.evidence["glyph_coverage"] == 0.80
    assert defect.suggested_action is not None
    assert report.attribution == "encoding_broken"


def test_cid_unmapped_ratio_triggers_encoding_loss() -> None:
    assert DefectCode.ENCODING_LOSS in codes(
        page_input(metrics=healthy_metrics(cid_unmapped_ratio=0.05))
    )


def test_low_density_uses_page_type_threshold() -> None:
    text_page = page_input(metrics=healthy_metrics(char_density=0.5))
    scanned_page = page_input(
        pdf_type=PdfType.SCANNED, metrics=healthy_metrics(char_density=0.5)
    )

    assert DefectCode.LOW_DENSITY in codes(text_page)
    assert DefectCode.LOW_DENSITY not in codes(scanned_page)


def test_empty_region_fails_with_bbox_when_available() -> None:
    report = L1_CHECKER.check(
        page_input(
            metrics=healthy_metrics(empty_region_ratio=0.4),
            bbox=(120.0, 340.0, 520.0, 610.0),
        )
    )

    defect = next(d for d in report.defects if d.code is DefectCode.REGION_EMPTY)
    assert defect.location.page_no == 5
    assert defect.location.bbox == (120.0, 340.0, 520.0, 610.0)


def test_table_fill_gate_only_applies_to_table_pages() -> None:
    metrics = healthy_metrics(table_cell_fill=0.4, row_occupancy=0.2)

    assert DefectCode.TABLE_STRUCTURE_BROKEN not in codes(page_input(metrics=metrics))
    table_page = page_input(metrics=metrics, has_table=True, table_index=2)
    report = L1_CHECKER.check(table_page)
    defect = next(
        d for d in report.defects if d.code is DefectCode.TABLE_STRUCTURE_BROKEN
    )
    assert defect.location.table_index == 2


def test_low_fill_with_complete_structure_is_not_broken() -> None:
    metrics = healthy_metrics(
        table_cell_fill=0.5, row_occupancy=1.0, col_occupancy=1.0
    )

    assert DefectCode.TABLE_STRUCTURE_BROKEN not in codes(
        page_input(metrics=metrics, has_table=True)
    )


def test_table_page_uses_lower_density_threshold() -> None:
    table_density = healthy_metrics(char_density=0.6)
    blank_table = healthy_metrics(char_density=0.4)

    assert DefectCode.LOW_DENSITY not in codes(
        page_input(metrics=table_density, has_table=True)
    )
    assert DefectCode.LOW_DENSITY in codes(
        page_input(metrics=blank_table, has_table=True)
    )


def test_text_based_density_threshold_is_calibrated() -> None:
    """text_based 阈值 0.9：稀疏但有效页（R-0077 1.064）通过，近空白页拦住。"""
    assert DefectCode.LOW_DENSITY not in codes(
        page_input(metrics=healthy_metrics(char_density=0.95))
    )
    assert DefectCode.LOW_DENSITY in codes(
        page_input(metrics=healthy_metrics(char_density=0.85))
    )


def test_mixed_density_threshold_is_calibrated() -> None:
    """mixed 阈值 0.5：R-0098 0.726 / R-0102 0.87 通过。"""
    assert DefectCode.LOW_DENSITY not in codes(
        page_input(
            pdf_type=PdfType.MIXED, metrics=healthy_metrics(char_density=0.6)
        )
    )
    assert DefectCode.LOW_DENSITY in codes(
        page_input(
            pdf_type=PdfType.MIXED, metrics=healthy_metrics(char_density=0.45)
        )
    )


def test_column_page_uses_lower_density_threshold() -> None:
    assert DefectCode.LOW_DENSITY not in codes(
        page_input(metrics=healthy_metrics(char_density=0.45), has_columns=True)
    )
    assert DefectCode.LOW_DENSITY in codes(
        page_input(metrics=healthy_metrics(char_density=0.35), has_columns=True)
    )


def test_scanned_blank_page_still_fails() -> None:
    """纯扫描页字符数为 0，必须继续命中（保留 needs_ocr 缺口）。"""
    assert DefectCode.LOW_DENSITY in codes(
        page_input(
            pdf_type=PdfType.SCANNED, metrics=healthy_metrics(char_density=0.0)
        )
    )


def test_reading_order_header_footer_and_rotation(  ) -> None:
    report = L1_CHECKER.check(
        page_input(
            metrics=healthy_metrics(
                reading_order_score=0.5, header_footer_leak=0.2
            ),
            rotation=90,
        )
    )
    found = {defect.code for defect in report.defects}

    assert found == {
        DefectCode.READING_ORDER_BROKEN,
        DefectCode.HEADER_FOOTER_LEAK,
        DefectCode.PAGE_ROTATED,
    }
    rotated = next(d for d in report.defects if d.code is DefectCode.PAGE_ROTATED)
    assert rotated.location.angle == 90.0


def test_page_number_broken_is_statistics_only() -> None:
    report = L1_CHECKER.check(
        page_input(metrics=healthy_metrics(page_number_ok=False))
    )

    assert DefectCode.PAGE_NUMBER_BROKEN in {d.code for d in report.defects}
    assert defects_for_repair(report) == ()


def test_every_defect_has_actionable_location() -> None:
    report = L1_CHECKER.check(
        page_input(
            metrics=healthy_metrics(
                glyph_coverage=0.2,
                char_density=0.1,
                empty_region_ratio=0.9,
                reading_order_score=0.1,
                header_footer_leak=0.5,
                page_number_ok=False,
            ),
            rotation=180,
        )
    )

    assert len(report.defects) >= 6
    assert all(defect.location.page_no == 5 for defect in report.defects)


def test_missing_metrics_lower_qc_confidence() -> None:
    partial = L1Metrics(glyph_coverage=0.99, char_density=8.0)

    report = L1_CHECKER.check(page_input(metrics=partial))

    assert 0.0 < report.qc_confidence < 1.0


def test_l1_is_deterministic_and_read_only() -> None:
    page = page_input(metrics=healthy_metrics(glyph_coverage=0.8))
    before = dataclasses.asdict(page)

    first = L1_CHECKER.check(page)
    second = L1_CHECKER.check(page)

    assert first == second
    assert dataclasses.asdict(page) == before


@pytest.mark.parametrize("overrides", [
    {"glyph_coverage": 0.0},
    {"char_density": 0.0},
    {"empty_region_ratio": 1.0},
    {"header_footer_leak": 1.0},
])
def test_severity_stays_in_range(overrides: dict[str, float]) -> None:
    report = L1_CHECKER.check(page_input(metrics=healthy_metrics(**overrides)))

    assert all(0.0 <= defect.severity <= 1.0 for defect in report.defects)
    assert 0.0 <= report.extract_quality <= 1.0


def test_from_text_populates_text_quality_features() -> None:
    metrics = L1Metrics.from_text(
        "第一段完整。\n第二段完整。\n", text_area=468.0
    )

    assert metrics.clean_spacing == 1.0
    assert metrics.prose_ratio == 1.0
    assert metrics.numbering_ratio == 0.0


def test_text_quality_features_affect_score() -> None:
    healthy = L1_CHECKER.check(page_input()).extract_quality
    degraded = L1_CHECKER.check(
        page_input(
            metrics=healthy_metrics(
                clean_spacing=0.0, prose_ratio=0.2, numbering_ratio=0.0
            )
        )
    ).extract_quality

    assert healthy > degraded


def test_table_fill_affects_table_page_score() -> None:
    good = L1_CHECKER.check(
        page_input(
            has_table=True,
            metrics=healthy_metrics(table_cell_fill=1.0),
        )
    ).extract_quality
    broken = L1_CHECKER.check(
        page_input(
            has_table=True,
            metrics=healthy_metrics(table_cell_fill=0.5),
        )
    ).extract_quality

    assert good > broken


def test_non_table_page_is_not_penalized_for_missing_table_metrics() -> None:
    report = L1_CHECKER.check(
        page_input(metrics=healthy_metrics(table_cell_fill=None))
    )

    assert report.extract_quality > 0.9
