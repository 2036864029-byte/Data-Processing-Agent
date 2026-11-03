"""TableGrid Markdown 渲染测试。"""

from __future__ import annotations

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.table.render import to_markdown


def test_render_plain_table() -> None:
    grid = TableGrid(
        page_no=1,
        bbox=(0, 0, 1, 1),
        n_rows=2,
        n_cols=2,
        header_rows=1,
        cells=(
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额"),
            TableCell(row=1, col=0, text="合计"),
            TableCell(row=1, col=1, text="100"),
        ),
        source_engine="test",
        confidence=1.0,
    )

    assert to_markdown(grid) == (
        "| 项目 | 金额 |\n| --- | --- |\n| 合计 | 100 |"
    )


def test_render_merged_cell_and_escaping() -> None:
    grid = TableGrid(
        page_no=1,
        bbox=(0, 0, 1, 1),
        n_rows=2,
        n_cols=2,
        header_rows=1,
        cells=(
            TableCell(row=0, col=0, text="a|b", colspan=2),
            TableCell(row=1, col=0, text="第一行\n第二行"),
            TableCell(row=1, col=1, text="x"),
        ),
        source_engine="test",
        confidence=1.0,
    )

    rendered = to_markdown(grid)

    assert "a\\|b" in rendered
    assert "第一行<br>第二行" in rendered
    assert rendered.splitlines()[2] == "| 第一行<br>第二行 | x |"
