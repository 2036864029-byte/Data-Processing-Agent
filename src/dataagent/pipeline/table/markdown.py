"""Markdown 管道表 → TableGrid（L0 快速路径）。

只解析结构，不做坐标推断；缺失单元格保留为空，供质量评分使用。
"""

from __future__ import annotations

import re
from typing import Final

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.quality.text_normalize import normalize_cjk_text

_SEPARATOR_CELL: Final[re.Pattern[str]] = re.compile(r"^:?-{3,}:?$")


def markdown_tables(
    markdown: str,
    *,
    page_no: int,
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    source_engine: str = "pdf-inspector.markdown",
) -> list[TableGrid]:
    grids: list[TableGrid] = []
    for header, rows in _parse_tables(markdown):
        n_cols = max(
            [len(header), *(len(row) for row in rows)], default=0
        )
        if n_cols == 0 or not rows:
            continue
        header_rows = 1 if any(cell.strip() for cell in header) else 0
        cells: list[TableCell] = []
        for row_index, row in enumerate([header, *rows]):
            for col_index, text in enumerate(row):
                value = normalize_cjk_text(text).strip()
                if value:
                    cells.append(
                        TableCell(
                            row=row_index,
                            col=col_index,
                            text=value,
                        )
                    )
        grids.append(
            TableGrid(
                page_no=page_no,
                bbox=bbox,
                n_rows=len(rows) + 1,
                n_cols=n_cols,
                header_rows=header_rows,
                cells=tuple(cells),
                source_engine=source_engine,
                confidence=0.7,
            )
        )
    return grids


def _parse_tables(markdown: str) -> list[tuple[list[str], list[list[str]]]]:
    lines = markdown.splitlines()
    tables: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    while index + 1 < len(lines):
        header_line = lines[index]
        separator_line = lines[index + 1]
        if "|" in header_line and _is_separator(separator_line):
            header = _split_row(header_line)
            rows: list[list[str]] = []
            cursor = index + 2
            while (
                cursor < len(lines)
                and "|" in lines[cursor]
                and lines[cursor].strip()
            ):
                rows.append(_split_row(lines[cursor]))
                cursor += 1
            tables.append((header, rows))
            index = cursor
            continue
        index += 1
    return tables


def _is_separator(line: str) -> bool:
    if "|" not in line:
        return False
    cells = _split_row(line)
    return bool(cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells)


def _split_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]
