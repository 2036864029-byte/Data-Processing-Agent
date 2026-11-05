"""逻辑行合并测试。"""

from __future__ import annotations

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.table.render import to_markdown
from dataagent.pipeline.table.rowmerge import merge_logical_rows


def _grid(cells: tuple[TableCell, ...], n_rows: int, n_cols: int) -> TableGrid:
    return TableGrid(
        page_no=1,
        bbox=(0, 0, 1, 1),
        n_rows=n_rows,
        n_cols=n_cols,
        header_rows=1,
        cells=cells,
        source_engine="test",
        confidence=1.0,
    )


def test_merges_rows_split_by_inner_rules() -> None:
    """姓名跨两行、职务与日期各占一行时，合并回一条记录并对位。"""
    grid = _grid(
        (
            TableCell(row=0, col=0, text="姓名"),
            TableCell(row=0, col=1, text="职务"),
            TableCell(row=0, col=2, text="任期起始"),
            TableCell(row=1, col=0, text="张德芹", rowspan=2),
            TableCell(row=1, col=1, text="党委书记"),
            TableCell(row=1, col=2, text="2024-04-30"),
            TableCell(row=2, col=1, text="董事长、董事"),
            TableCell(row=2, col=2, text="2024-05-29"),
        ),
        n_rows=3,
        n_cols=3,
    )

    merged = merge_logical_rows(grid)

    assert merged.n_rows == 2
    assert to_markdown(merged) == (
        "| 姓名 | 职务 | 任期起始 |\n| --- | --- | --- |\n"
        "| 张德芹 | 党委书记<br>董事长、董事 | 2024-04-30<br>2024-05-29 |"
    )


def test_keeps_rows_without_vertical_merge() -> None:
    """全表没有合并单元格时不合并，空首列行也各自成行。"""
    grid = _grid(
        (
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额"),
            TableCell(row=1, col=0, text="合计"),
            TableCell(row=1, col=1, text="100"),
            TableCell(row=2, col=1, text="200"),
        ),
        n_rows=3,
        n_cols=2,
    )

    assert merge_logical_rows(grid) is grid


def test_merges_three_way_rowspan() -> None:
    """跨三行的职务与三个日期按顺序对位。"""
    grid = _grid(
        (
            TableCell(row=0, col=0, text="姓名"),
            TableCell(row=0, col=1, text="职务"),
            TableCell(row=1, col=0, text="蒋焰", rowspan=3),
            TableCell(row=1, col=1, text="党委委员"),
            TableCell(row=1, col=2, text="2022-11-03"),
            TableCell(row=2, col=1, text="副总经理、财务总监"),
            TableCell(row=2, col=2, text="2021-11-15"),
            TableCell(row=3, col=1, text="董事会秘书"),
            TableCell(row=3, col=2, text="2022-01-25"),
        ),
        n_rows=4,
        n_cols=3,
    )

    merged = merge_logical_rows(grid)

    assert merged.n_rows == 2
    row = {cell.col: cell.text for cell in merged.cells if cell.row == 1}
    assert row == {
        0: "蒋焰",
        1: "党委委员\n副总经理、财务总监\n董事会秘书",
        2: "2022-11-03\n2021-11-15\n2022-01-25",
    }


def test_single_row_grid_untouched() -> None:
    grid = _grid((TableCell(row=0, col=0, text="项目"),), n_rows=1, n_cols=1)
    assert merge_logical_rows(grid) is grid


def test_multi_row_header_is_preserved() -> None:
    grid = TableGrid(
        page_no=1,
        bbox=(0, 0, 1, 1),
        n_rows=4,
        n_cols=2,
        header_rows=2,
        cells=(
            TableCell(row=0, col=0, text="项目", rowspan=2),
            TableCell(row=0, col=1, text="金额"),
            TableCell(row=1, col=1, text="本期"),
            TableCell(row=2, col=0, text="A", rowspan=2),
            TableCell(row=2, col=1, text="1"),
            TableCell(row=3, col=1, text="2"),
        ),
        source_engine="test",
        confidence=1.0,
    )

    merged = merge_logical_rows(grid)

    assert merged.n_rows == 3
    assert merged.header_rows == 2
    header = {
        (cell.row, cell.col): cell.text
        for cell in merged.cells
        if cell.row < 2
    }
    assert header == {(0, 0): "项目", (0, 1): "金额", (1, 1): "本期"}
    data = {cell.col: cell.text for cell in merged.cells if cell.row == 2}
    assert data == {0: "A", 1: "1\n2"}


def test_colspan_rows_are_not_merged() -> None:
    grid = TableGrid(
        page_no=1,
        bbox=(0, 0, 1, 1),
        n_rows=3,
        n_cols=3,
        header_rows=1,
        cells=(
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="值"),
            TableCell(row=1, col=0, text="A", rowspan=2),
            TableCell(row=1, col=1, text="x", colspan=2),
            TableCell(row=2, col=1, text="y"),
            TableCell(row=2, col=2, text="z"),
        ),
        source_engine="test",
        confidence=1.0,
    )

    assert merge_logical_rows(grid) is grid


def test_header_rowspan_into_data_is_truncated() -> None:
    grid = TableGrid(
        page_no=1,
        bbox=(0, 0, 1, 1),
        n_rows=3,
        n_cols=2,
        header_rows=1,
        cells=(
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额", rowspan=2),
            TableCell(row=1, col=0, text="A", rowspan=2),
            TableCell(row=2, col=1, text="2"),
        ),
        source_engine="test",
        confidence=1.0,
    )

    merged = merge_logical_rows(grid)

    assert merged.n_rows == 2
    assert merged.header_rows == 1
    header = {(cell.row, cell.col): cell for cell in merged.cells if cell.row == 0}
    assert header[(0, 1)].rowspan == 1
    data = {cell.col: cell.text for cell in merged.cells if cell.row == 1}
    assert data == {0: "A", 1: "2"}
