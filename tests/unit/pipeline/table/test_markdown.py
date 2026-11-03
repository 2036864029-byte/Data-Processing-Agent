"""Markdown 表解析测试。"""

from __future__ import annotations

from dataagent.pipeline.table.markdown import markdown_tables


def test_parse_single_table() -> None:
    grids = markdown_tables(
        "前文\n| 项目 | 金额 |\n|---|---|\n| 合计 | 100 |\n后文\n",
        page_no=3,
    )

    assert len(grids) == 1
    grid = grids[0]
    assert (grid.n_rows, grid.n_cols) == (2, 2)
    assert grid.header_rows == 1
    assert grid.page_no == 3
    assert {cell.text for cell in grid.cells} == {"项目", "金额", "合计", "100"}


def test_empty_header_is_not_counted_as_header_row() -> None:
    grids = markdown_tables(
        "| | |\n|---|---|\n| 项目 | 金额 |\n", page_no=1
    )

    assert grids[0].header_rows == 0


def test_multiple_tables_and_ragged_rows() -> None:
    markdown = (
        "| a | b |\n|---|---|\n| 1 |\n\n"
        "| c | d |\n|---|---|\n| 2 | 3 | 4 |\n"
    )

    grids = markdown_tables(markdown, page_no=1)

    assert len(grids) == 2
    assert grids[0].n_cols == 2
    assert grids[1].n_cols == 3


def test_no_table_returns_empty() -> None:
    assert markdown_tables("plain text\n", page_no=1) == []
