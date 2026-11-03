"""表格质量评分测试。"""

from __future__ import annotations

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.table.quality import grid_quality


def grid(*cells: TableCell, header_rows: int = 1, confidence: float = 1.0) -> TableGrid:
    return TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 10.0, 10.0),
        n_rows=2,
        n_cols=2,
        header_rows=header_rows,
        cells=cells,
        source_engine="test",
        confidence=confidence,
    )


def test_full_grid_scores_high() -> None:
    quality = grid_quality(
        grid(
            TableCell(row=0, col=0, text="a"),
            TableCell(row=0, col=1, text="b"),
            TableCell(row=1, col=0, text="1"),
            TableCell(row=1, col=1, text="2"),
        )
    )

    assert quality.cell_fill == 1.0
    assert quality.header_ok is True
    assert quality.score == 1.0


def test_empty_header_lowers_score() -> None:
    quality = grid_quality(
        grid(
            TableCell(row=1, col=0, text="1"),
            TableCell(row=1, col=1, text="2"),
            header_rows=0,
        )
    )

    assert quality.header_ok is False
    assert quality.cell_fill == 0.5
    assert quality.score < 0.7


def test_partial_cells_lower_fill() -> None:
    quality = grid_quality(
        grid(
            TableCell(row=0, col=0, text="a"),
            TableCell(row=1, col=1, text="2"),
        )
    )

    assert quality.cell_fill == 0.5
