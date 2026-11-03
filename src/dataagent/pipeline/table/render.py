"""TableGrid 渲染：Markdown 表格（人类视图）。

TableGrid 是唯一真源；Markdown 只用于展示/核验。
- 合并单元格：文本放左上角，其余占位为空；
- 表头按 header_rows 渲染分隔行；header_rows=0 时首行作为展示表头；
- 单元格内换行替换为 <br>，管道符转义。
"""

from __future__ import annotations

from dataagent.contracts.table import TableGrid


def to_markdown(grid: TableGrid) -> str:
    matrix = _matrix(grid)
    header_rows = max(1, grid.header_rows)
    lines: list[str] = []
    for row_index, row in enumerate(matrix):
        lines.append(_row(row))
        if row_index + 1 == min(header_rows, grid.n_rows):
            lines.append(_row(["---"] * grid.n_cols))
    return "\n".join(lines)


def _matrix(grid: TableGrid) -> list[list[str]]:
    matrix = [["" for _ in range(grid.n_cols)] for _ in range(grid.n_rows)]
    for cell in grid.cells:
        matrix[cell.row][cell.col] = _escape(cell.text)
    return matrix


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "<br>")
        .strip()
    )
