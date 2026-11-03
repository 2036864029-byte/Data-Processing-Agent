"""表格验收网关测试：结构、内容覆盖、行覆盖、多值单元格与 bbox 合理性。"""

from __future__ import annotations

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.table.acceptance import (
    GateDecision,
    evaluate_candidate,
)


def grid(
    rows: list[list[str]],
    *,
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 100.0, 100.0),
) -> TableGrid:
    n_rows = len(rows)
    n_cols = max((len(row) for row in rows), default=1)
    cells = tuple(
        TableCell(row=row_index, col=col_index, text=text)
        for row_index, row in enumerate(rows)
        for col_index, text in enumerate(row)
        if text
    )
    return TableGrid(
        page_no=1,
        bbox=bbox,
        n_rows=n_rows,
        n_cols=n_cols,
        header_rows=1,
        cells=cells,
        source_engine="test",
        confidence=0.9,
    )


def test_accepts_candidate_with_similar_reference() -> None:
    reference = grid([["项目", "金额"], ["合计", "100"]])
    candidate = grid([["项目", "金额"], ["合计", "100"], ["备注", "无"]])

    decision = evaluate_candidate(candidate, reference, page_bbox=(0.0, 0.0, 100.0, 100.0))

    assert decision.accepted is True
    assert decision.reason == "accepted"
    assert decision.stats.text_coverage == 1.0


def test_accepts_when_reference_contains_title_row() -> None:
    reference = TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 100.0, 130.0),
        n_rows=5,
        n_cols=2,
        header_rows=1,
        cells=(
            TableCell(
                row=0,
                col=0,
                text="贵州茅台酒股份有限公司2024年年度报告",
                colspan=2,
                bbox=(0.0, 110.0, 100.0, 120.0),
            ),
            TableCell(row=1, col=0, text="股东会届次", bbox=(0.0, 90.0, 50.0, 100.0)),
            TableCell(row=1, col=1, text="披露日期", bbox=(50.0, 90.0, 100.0, 100.0)),
            TableCell(
                row=2, col=0, text="2023年度股东大会", bbox=(0.0, 80.0, 50.0, 90.0)
            ),
            TableCell(row=2, col=1, text="2024-05-29", bbox=(50.0, 80.0, 100.0, 90.0)),
            TableCell(
                row=3,
                col=0,
                text="2024年第一次临时股东大会",
                bbox=(0.0, 70.0, 50.0, 80.0),
            ),
            TableCell(row=3, col=1, text="2024-11-27", bbox=(50.0, 70.0, 100.0, 80.0)),
            TableCell(row=4, col=0, text="其他说明", bbox=(0.0, 60.0, 50.0, 70.0)),
            TableCell(row=4, col=1, text="无", bbox=(50.0, 60.0, 100.0, 70.0)),
        ),
        source_engine="test",
        confidence=0.9,
    )
    candidate = grid(
        [
            ["股东会届次", "披露日期"],
            ["2023年度股东大会", "2024-05-29"],
            ["2024年第一次临时股东大会", "2024-11-27"],
        ]
    )

    decision = evaluate_candidate(candidate, reference, page_bbox=(0.0, 0.0, 100.0, 130.0))

    assert decision.accepted is True
    assert decision.stats.text_coverage is not None
    assert decision.stats.text_coverage >= 0.85


def test_rejects_candidate_that_lost_text() -> None:
    reference = grid([["abcdefghij", "klmnopqrst"], ["uvwxyz", "0123456789"]])
    candidate = grid([["abc", "def"], ["ghi", "jkl"]])

    decision = evaluate_candidate(candidate, reference)

    assert decision.accepted is False
    assert decision.reason == "text_loss"


def test_rejects_fragment_with_few_rows() -> None:
    reference = grid(
        [["0123456789", "a"], *[["", ""] for _ in range(9)]]
    )
    candidate = grid([["0123456789", "a"], ["", ""]])

    decision = evaluate_candidate(candidate, reference)

    assert decision.accepted is False
    assert decision.reason == "row_loss"


def test_rejects_cell_with_multiple_dates() -> None:
    candidate = grid([["姓名", "任期"], ["张德芹", "2024年05月29日 2023年08月17日"]])

    decision = evaluate_candidate(candidate, None)

    assert decision.accepted is False
    assert decision.reason == "multi_value_cell"


def test_rejects_cell_with_multiple_amounts() -> None:
    candidate = grid([["项目", "金额"], ["银行存款", "59,295,750,908.09 62,315.80"]])

    decision = evaluate_candidate(candidate, None)

    assert decision.accepted is False
    assert decision.reason == "multi_value_cell"


def test_rejects_sparse_grid() -> None:
    rows = [["A"] + [""] * 9 for _ in range(10)]
    candidate = grid(rows)

    decision = evaluate_candidate(candidate, None)

    assert decision.accepted is False
    assert decision.reason == "low_fill"


def test_rejects_degenerate_table() -> None:
    candidate = grid([["A", "B", "C"]])

    decision = evaluate_candidate(candidate, None)

    assert decision.accepted is False
    assert decision.reason == "too_small"


def test_rejects_bbox_outside_page() -> None:
    candidate = grid(
        [["A", "B"], ["C", "D"]],
        bbox=(200.0, 0.0, 300.0, 100.0),
    )

    decision = evaluate_candidate(candidate, None, page_bbox=(0.0, 0.0, 100.0, 100.0))

    assert decision.accepted is False
    assert decision.reason == "bbox_outside"


def test_accepts_without_reference() -> None:
    candidate = grid([["A", "B"], ["C", "D"]])

    decision = evaluate_candidate(candidate, None)

    assert decision.accepted is True


def test_decision_exposes_stats() -> None:
    reference = grid([["项目", "金额"], ["合计", "100"]])
    candidate = grid([["项目", "金额"], ["合计", "100"]])

    decision = evaluate_candidate(candidate, reference)

    assert isinstance(decision, GateDecision)
    assert decision.stats.n_rows == 2
    assert decision.stats.n_cols == 2
    assert decision.stats.row_ratio is not None
    assert decision.stats.multi_value_cells == 0
