"""L1 表格硬门控测试：填充率 / 列数方差 / 表头缺失均触发 table_structure_broken。"""

from __future__ import annotations

import pytest

from dataagent.contracts.errors import DefectCode
from dataagent.contracts.messages import Verdict
from dataagent.pipeline.quality.l1_check import (
    L1_CHECKER,
    L1Metrics,
    L1PageInput,
)

DOC_ID = "a" * 64


def table_page(**overrides: object) -> L1PageInput:
    values: dict[str, object] = {
        "char_density": 8.0,
        "glyph_coverage": 0.995,
        "cid_unmapped_ratio": 0.001,
        "empty_region_ratio": 0.05,
        "table_cell_fill": 0.95,
        "row_col_consistency": 0.0,
        "table_header_ok": True,
        "reading_order_score": 0.97,
        "bbox_coverage": 0.99,
    }
    values.update(overrides)
    return L1PageInput(
        doc_id=DOC_ID,
        page_no=3,
        metrics=L1Metrics(**values),  # type: ignore[arg-type]
        has_table=True,
    )


def codes(page: L1PageInput) -> tuple[DefectCode, ...]:
    report = L1_CHECKER.check(page)
    return tuple(defect.code for defect in report.defects)


def test_healthy_table_passes() -> None:
    report = L1_CHECKER.check(table_page())
    assert report.verdict is Verdict.PASS
    assert DefectCode.TABLE_STRUCTURE_BROKEN not in codes(table_page())


def test_low_cell_fill_fails() -> None:
    assert DefectCode.TABLE_STRUCTURE_BROKEN in codes(table_page(table_cell_fill=0.5))


def test_high_column_variance_fails() -> None:
    assert DefectCode.TABLE_STRUCTURE_BROKEN in codes(
        table_page(row_col_consistency=2.0)
    )


def test_missing_header_fails() -> None:
    report = L1_CHECKER.check(table_page(table_header_ok=False))
    assert report.verdict is Verdict.FAIL
    defect = next(
        item
        for item in report.defects
        if item.code is DefectCode.TABLE_STRUCTURE_BROKEN
    )
    assert defect.evidence["table_header_ok"] == "false"


def test_table_metrics_ignored_on_non_table_page() -> None:
    page = table_page(table_cell_fill=0.2, table_header_ok=False)
    page = L1PageInput(
        doc_id=page.doc_id,
        page_no=page.page_no,
        metrics=page.metrics,
        has_table=False,
    )
    with pytest.raises(StopIteration):
        next(
            defect
            for defect in L1_CHECKER.check(page).defects
            if defect.code is DefectCode.TABLE_STRUCTURE_BROKEN
        )
