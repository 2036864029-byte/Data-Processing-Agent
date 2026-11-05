"""Markdown 表格指标（T127 后续 / qc-output.md 口径）。

从产物 Markdown 解析管道表，产出 L1 需要的两个表格指标：

- `table_cell_fill` = 非空单元格 / 总单元格（总单元格按最大列数 × 行数计，
  缺列按空格惩罚）；
- `row_col_consistency` = 每行列数方差（契约口径，越小越好）；
- `table_header_ok` = 表头非空且分隔行合法（否则视为表头丢失）。

一页多表时取最差值（最低填充率、最高方差、任一表头缺失）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_SEPARATOR_CELL: Final[re.Pattern[str]] = re.compile(r"^:?-{3,}:?$")
_PIPE: Final[str] = "|"


@dataclass(frozen=True, slots=True)
class TableMetrics:
    tables: int
    cell_fill: float
    column_variance: float
    header_ok: bool
    row_occupancy: float = 1.0
    col_occupancy: float = 1.0


def table_page_metrics(markdown: str) -> TableMetrics | None:
    """解析 Markdown 中的全部管道表；无表返回 None。"""
    tables = _parse_tables(markdown)
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
    return bool(cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells)


def _split_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith(_PIPE):
        text = text[1:]
    if text.endswith(_PIPE):
        text = text[:-1]
    return [cell.strip() for cell in text.split(_PIPE)]


def _header_ok(header: list[str], separator: list[str]) -> bool:
    return any(cell for cell in header) and bool(separator) and all(
        _SEPARATOR_CELL.match(cell) for cell in separator
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
