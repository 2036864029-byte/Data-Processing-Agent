"""Markdown 表格指标（T127 后续 / qc-output.md 口径）。

从产物 Markdown 解析管道表，产出 L1 需要的两个表格指标：

- `table_cell_fill` = 非空单元格 / 总单元格（总单元格按最大列数 × 行数计，
  缺列按空格惩罚）；
- `row_col_consistency` = 每行列数方差（契约口径，越小越好）；
- `table_header_ok` = 表头非空且分隔行合法（否则视为表头丢失）。

一页多表时取最差值（最低填充率、最高方差、任一表头缺失）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.table.policy import DEFAULT_TABLE_POLICY, TablePolicy
from dataagent.pipeline.table.prose import is_prose_cells
from dataagent.pipeline.table.syntax import is_separator_cell

TABLE_CELL_FILL_MIN: Final[float] = 0.90
ROW_COL_VARIANCE_MAX: Final[float] = 1.0
ROW_OCCUPANCY_MIN: Final[float] = 0.8
COL_OCCUPANCY_MIN: Final[float] = 0.8
_PIPE: Final[str] = "|"


@dataclass(frozen=True, slots=True)
class TableMetrics:
    tables: int
    cell_fill: float
    column_variance: float
    header_ok: bool
    row_occupancy: float = 1.0
    col_occupancy: float = 1.0


def table_structure_ok(metrics: TableMetrics) -> bool:
    """与 L1 表格门控同源的“结构是否可用”判定。"""
    fill_bad = metrics.cell_fill < TABLE_CELL_FILL_MIN and (
        metrics.row_occupancy < ROW_OCCUPANCY_MIN
        or metrics.col_occupancy < COL_OCCUPANCY_MIN
    )
    return (
        not fill_bad
        and metrics.column_variance <= ROW_COL_VARIANCE_MAX
        and metrics.header_ok
    )


def grid_table_metrics(
    grids: Sequence[TableGrid],
) -> TableMetrics | None:
    """从最终 TableGrid 直接计算表格指标（与 L0 Markdown 口径并行）。"""
    if not grids:
        return None
    fills: list[float] = []
    row_occupancies: list[float] = []
    col_occupancies: list[float] = []
    headers: list[bool] = []
    for grid in grids:
        matrix = _grid_matrix(grid)
        total = grid.n_rows * grid.n_cols
        filled = sum(
            cell.rowspan * cell.colspan
            for cell in grid.cells
            if cell.text.strip()
        )
        fills.append(filled / total if total else 0.0)
        row_occupancies.append(_matrix_row_occupancy(matrix))
        col_occupancies.append(_matrix_col_occupancy(matrix))
        header_rows = min(max(1, grid.header_rows), grid.n_rows)
        headers.append(
            any(
                matrix[row][col].strip()
                for row in range(header_rows)
                for col in range(grid.n_cols)
            )
        )
    return TableMetrics(
        tables=len(grids),
        cell_fill=round(min(fills), 6),
        column_variance=0.0,
        header_ok=all(headers),
        row_occupancy=round(min(row_occupancies), 6),
        col_occupancy=round(min(col_occupancies), 6),
    )


def _grid_matrix(grid: TableGrid) -> list[list[str]]:
    matrix = [["" for _ in range(grid.n_cols)] for _ in range(grid.n_rows)]
    for cell in grid.cells:
        for row in range(cell.row, min(grid.n_rows, cell.row + cell.rowspan)):
            for col in range(cell.col, min(grid.n_cols, cell.col + cell.colspan)):
                matrix[row][col] = cell.text
    return matrix


def _matrix_row_occupancy(matrix: Sequence[Sequence[str]]) -> float:
    if not matrix:
        return 0.0
    filled = sum(1 for row in matrix if any(cell.strip() for cell in row))
    return filled / len(matrix)


def _matrix_col_occupancy(matrix: Sequence[Sequence[str]]) -> float:
    if not matrix or not matrix[0]:
        return 0.0
    columns = len(matrix[0])
    filled = sum(
        1
        for col in range(columns)
        if any(matrix[row][col].strip() for row in range(len(matrix)))
    )
    return filled / columns


def table_page_metrics(
    markdown: str,
    *,
    skip_prose: bool = False,
    policy: TablePolicy = DEFAULT_TABLE_POLICY,
) -> TableMetrics | None:
    """解析 Markdown 中的全部管道表；无表返回 None。

    `skip_prose=True` 时先剔除伪表格（页眉假表 / 整页正文被识别成表），
    与建块阶段的降级判据同源（`pipeline.table.prose`）。L1 必须用它，
    否则会出现“L1 说表结构坏、交付里却没有表”的假缺口（R-0002）。
    """
    tables = _parse_tables(markdown)
    if skip_prose:
        tables = [
            table for table in tables if not _is_prose_table(table, policy)
        ]
    if not tables:
        return None
    fills: list[float] = []
    variances: list[float] = []
    row_occupancies: list[float] = []
    col_occupancies: list[float] = []
    headers_ok = True
    for header, separator, rows in tables:
        fills.append(_cell_fill(header, rows))
        variances.append(_column_variance(header, rows))
        row_occupancies.append(_row_occupancy(header, rows))
        col_occupancies.append(_col_occupancy(header, rows))
        headers_ok = headers_ok and _header_ok(header, separator)
    return TableMetrics(
        tables=len(tables),
        cell_fill=min(fills),
        column_variance=max(variances),
        header_ok=headers_ok,
        row_occupancy=min(row_occupancies),
        col_occupancy=min(col_occupancies),
    )


def _is_prose_table(
    table: tuple[list[str], list[str], list[list[str]]],
    policy: TablePolicy,
) -> bool:
    header, _separator, rows = table
    return is_prose_cells(
        [*header, *(cell for row in rows for cell in row)], policy=policy
    )


def _parse_tables(
    markdown: str,
) -> list[tuple[list[str], list[str], list[list[str]]]]:
    lines = markdown.splitlines()
    tables: list[tuple[list[str], list[str], list[list[str]]]] = []
    index = 0
    while index + 1 < len(lines):
        header_line = lines[index]
        separator_line = lines[index + 1]
        if _PIPE in header_line and _is_separator(separator_line):
            header = _split_row(header_line)
            separator = _split_row(separator_line)
            rows: list[list[str]] = []
            cursor = index + 2
            while cursor < len(lines) and _PIPE in lines[cursor] and lines[cursor].strip():
                rows.append(_split_row(lines[cursor]))
                cursor += 1
            tables.append((header, separator, rows))
            index = cursor
            continue
        index += 1
    return tables


def _is_separator(line: str) -> bool:
    if _PIPE not in line:
        return False
    cells = _split_row(line)
    return bool(cells) and all(is_separator_cell(cell) for cell in cells)


def _split_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith(_PIPE):
        text = text[1:]
    if text.endswith(_PIPE):
        text = text[:-1]
    return [cell.strip() for cell in text.split(_PIPE)]


def _header_ok(header: list[str], separator: list[str]) -> bool:
    return any(cell for cell in header) and bool(separator) and all(
        is_separator_cell(cell) for cell in separator
    )


def _cell_fill(header: list[str], rows: list[list[str]]) -> float:
    """数据行填充率；无数据行时退回表头（表头有效性另由 header_ok 判断）。"""
    evaluated = rows if rows else [header]
    columns = max((len(row) for row in evaluated), default=0)
    if columns == 0 or not evaluated:
        return 0.0
    total = columns * len(evaluated)
    filled = sum(1 for row in evaluated for cell in row if cell)
    return round(filled / total, 6)


def _column_variance(header: list[str], rows: list[list[str]]) -> float:
    counts = [len(row) for row in [header, *rows]]
    if len(counts) < 2:
        return 0.0
    mean = sum(counts) / len(counts)
    return round(sum((count - mean) ** 2 for count in counts) / len(counts), 6)


def _row_occupancy(header: list[str], rows: list[list[str]]) -> float:
    """有任意非空单元格的行占比。"""
    evaluated = [header, *rows]
    if not evaluated:
        return 0.0
    filled = sum(1 for row in evaluated if any(cell.strip() for cell in row))
    return round(filled / len(evaluated), 6)


def _col_occupancy(header: list[str], rows: list[list[str]]) -> float:
    """有任意非空单元格的列占比。"""
    evaluated = [header, *rows]
    columns = max((len(row) for row in evaluated), default=0)
    if columns == 0:
        return 0.0
    filled = sum(
        1
        for index in range(columns)
        if any(index < len(row) and row[index].strip() for row in evaluated)
    )
    return round(filled / columns, 6)
