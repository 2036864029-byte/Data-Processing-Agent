"""TableGrid → HTML 渲染测试（PR-1 阶段一：契约第三版表格形态）。"""

from __future__ import annotations

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.table.html import to_html


def grid(
    cells: tuple[TableCell, ...],
    *,
    n_rows: int = 2,
    n_cols: int = 3,
    header_rows: int = 1,
) -> TableGrid:
    return TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 100.0, 100.0),
        n_rows=n_rows,
        n_cols=n_cols,
        header_rows=header_rows,
        cells=cells,
        source_engine="test",
        confidence=1.0,
    )


def test_renders_rowspan_and_colspan_without_duplicates() -> None:
    html = to_html(
        grid(
            (
                TableCell(row=0, col=0, text="项目", rowspan=2),
                TableCell(row=0, col=1, text="期末余额", colspan=2),
                TableCell(row=1, col=1, text="金额"),
                TableCell(row=1, col=2, text="占比"),
                TableCell(row=2, col=0, text="货币资金"),
                TableCell(row=2, col=1, text="100"),
                TableCell(row=2, col=2, text="50%"),
            ),
            n_rows=3,
        )
    )

    assert '<th rowspan="2">项目</th>' in html
    assert '<th colspan="2">期末余额</th>' in html
    assert html.count("项目") == 1
    assert html.count("期末余额") == 1
    assert "<td>金额</td>" in html
    assert "<td>货币资金</td>" in html


def test_escapes_html_and_renders_newlines() -> None:
    html = to_html(
        grid(
            (TableCell(row=0, col=0, text="<b>&收入</b>\n第二行"),),
            n_rows=1,
            n_cols=1,
        )
    )

    assert "&lt;b&gt;&amp;收入&lt;/b&gt;<br>第二行" in html
    assert "<b>" not in html


def test_sparse_cells_are_filled_empty() -> None:
    html = to_html(grid((TableCell(row=0, col=0, text="A"),), n_rows=2, n_cols=2))

    assert html.count("<th></th>") == 1
    assert html.count("<td></td>") == 2


def test_header_rows_zero_renders_all_td() -> None:
    html = to_html(
        grid(
            (TableCell(row=0, col=0, text="A"), TableCell(row=1, col=0, text="B")),
            n_rows=2,
            n_cols=1,
            header_rows=0,
        )
    )

    assert "<th" not in html
    assert "<td>A</td>" in html
    assert "<td>B</td>" in html


def test_output_is_deterministic() -> None:
    cells = (
        TableCell(row=0, col=0, text="项目"),
        TableCell(row=1, col=0, text="值"),
    )
    first = to_html(grid(cells, n_rows=2, n_cols=1))
    second = to_html(grid(cells, n_rows=2, n_cols=1))

    assert first == second
    assert first.startswith("<table>")
    assert first.endswith("</table>")
