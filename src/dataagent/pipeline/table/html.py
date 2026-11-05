"""TableGrid → HTML（PR-1：契约第三版表格形态）。

无损渲染 rowspan/colspan；稀疏网格补空单元格；文本做 HTML 转义与换行转换。
纯函数、幂等，不做质量判断。
"""

from __future__ import annotations

import html

from dataagent.contracts.table import TableCell, TableGrid


def to_html(grid: TableGrid) -> str:
    cells = {(cell.row, cell.col): cell for cell in grid.cells}
    covered: set[tuple[int, int]] = set()
    rows: list[str] = []
    for row in range(grid.n_rows):
        parts: list[str] = []
        for col in range(grid.n_cols):
            if (row, col) in covered:
                continue
            cell = cells.get((row, col))
            tag = "th" if row < grid.header_rows else "td"
            if cell is None:
                parts.append(f"<{tag}></{tag}>")
                continue
            _mark_covered(cell, covered)
            parts.append(f"<{tag}{_attributes(cell)}>{_text(cell.text)}</{tag}>")
        rows.append("<tr>" + "".join(parts) + "</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def _mark_covered(cell: TableCell, covered: set[tuple[int, int]]) -> None:
    for row in range(cell.row, cell.row + cell.rowspan):
        for col in range(cell.col, cell.col + cell.colspan):
            if (row, col) != (cell.row, cell.col):
                covered.add((row, col))


def _attributes(cell: TableCell) -> str:
    attributes = ""
    if cell.rowspan > 1:
        attributes += f' rowspan="{cell.rowspan}"'
    if cell.colspan > 1:
        attributes += f' colspan="{cell.colspan}"'
    return attributes


def _text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(normalized, quote=False).replace("\n", "<br>")
