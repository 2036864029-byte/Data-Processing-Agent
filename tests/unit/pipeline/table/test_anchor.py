"""L0 锚点投影测试：bbox 回填、覆盖率、空白归一。"""

from __future__ import annotations

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.table.anchor import project_grid
from dataagent.pipeline.table.geometry import PositionedText


def grid() -> TableGrid:
    return TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 1.0, 1.0),
        n_rows=2,
        n_cols=2,
        header_rows=1,
        cells=(
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额"),
            TableCell(row=1, col=0, text="合计"),
            TableCell(row=1, col=1, text="100"),
        ),
        source_engine="markdown",
        confidence=0.7,
    )


def item(text: str, x: float, y: float) -> PositionedText:
    return PositionedText(text=text, x=x, y=y, width=40.0, height=10.0)


def test_projection_fills_cell_bboxes() -> None:
    items = [
        item("项目", 0, 330),
        item("金额", 50, 330),
        item("合计", 0, 314),
        item("100", 50, 314),
    ]

    projected = project_grid(grid(), items)

    assert projected is not None
    assert projected.coverage == 1.0
    assert projected.grid.bbox == (0.0, 314.0, 90.0, 340.0)
    first = next(cell for cell in projected.grid.cells if cell.text == "项目")
    assert first.bbox == (0.0, 330.0, 40.0, 340.0)


def test_projection_matches_across_spaces() -> None:
    items = [item("项 目", 0, 330), item("金 额", 50, 330)]

    projected = project_grid(grid(), items)

    assert projected is not None
    assert projected.coverage == 0.5


def test_projection_returns_none_without_matches() -> None:
    assert project_grid(grid(), [item("无关文字", 0, 100)]) is None
    assert project_grid(grid(), []) is None
