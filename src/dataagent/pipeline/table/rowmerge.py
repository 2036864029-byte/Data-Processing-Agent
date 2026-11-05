"""逻辑行合并：把「一条业务记录被拆成多个物理行」的表格还原成逻辑行。

中文财报表格里常见「行内短横线」：职务、任期日期这类列在单元格内部再分行，
横线只画在那几列里；姓名、性别、年龄是同一条记录的跨行合并单元格。
pdfplumber 忠实返回最细的物理网格（13 条人员记录 → 21 个物理行），
网关只校验「字有没有丢」，看不出这种行膨胀，必须在这一层合并回来。

合并判据（保守，两条同时满足才合并）：
1. key 列（默认首列）在当前物理行没有新值；
2. 当前物理行至少有一格被上方单元格的 rowspan 覆盖，即确有纵向合并证据。

第 2 条保证「整页没有合并单元格」的表格（如资产负债表明细）不会被误合并。

合并后同一列的多行文本用换行拼接，保持「第 n 行职务 ↔ 第 n 个日期」的对位关系。
"""

from __future__ import annotations

from dataagent.contracts.table import TableCell, TableGrid

TEXT_JOINER = "\n"


def merge_logical_rows(grid: TableGrid) -> TableGrid:
    """按 rowspan 证据把物理行合并为逻辑行；表头行原样保留，异常时保守回退。"""
    try:
        return _merge_logical_rows(grid)
    except ValueError:
        return grid


def _merge_logical_rows(grid: TableGrid) -> TableGrid:
    if grid.n_rows <= 1 or not grid.cells:
        return grid
    header_rows = min(grid.header_rows, grid.n_rows)
    if grid.n_rows - header_rows <= 1:
        return grid
    header_cells = tuple(
        cell.model_copy(
            update={"rowspan": min(cell.rowspan, header_rows - cell.row)}
        )
        for cell in grid.cells
        if cell.row < header_rows
    )
    data_cells = tuple(
        cell.model_copy(update={"row": cell.row - header_rows})
        for cell in grid.cells
        if cell.row >= header_rows
    )
    data_grid = grid.model_copy(
        update={
            "n_rows": grid.n_rows - header_rows,
            "header_rows": 0,
            "cells": data_cells,
        }
    )
    merged = _merge_data_rows(data_grid)
    if merged is data_grid:
        return grid
    cells = header_cells + tuple(
        cell.model_copy(update={"row": cell.row + header_rows})
        for cell in merged.cells
    )
    return TableGrid(
        page_no=grid.page_no,
        page_span=grid.page_span,
        bbox=grid.bbox,
        n_rows=header_rows + merged.n_rows,
        n_cols=grid.n_cols,
        header_rows=header_rows,
        cells=cells,
        source_engine=grid.source_engine,
        confidence=grid.confidence,
    )


def _merge_data_rows(grid: TableGrid) -> TableGrid:
    if grid.n_rows <= 1 or not grid.cells:
        return grid
    key_col = _key_column(grid)
    if key_col is None:
        return grid
    starts = {(cell.row, cell.col): cell for cell in grid.cells}
    covered = _span_covered(grid)
    groups = _group_rows(grid, key_col, starts, covered)
    if len(groups) == grid.n_rows:
        return grid

    cells: list[TableCell] = []
    for row_index, group in enumerate(groups):
        for col, (texts, colspan) in sorted(_buckets(group, starts, grid).items()):
            cells.append(
                TableCell(
                    row=row_index,
                    col=col,
                    text=TEXT_JOINER.join(texts),
                    colspan=colspan,
                )
            )
    return TableGrid(
        page_no=grid.page_no,
        page_span=grid.page_span,
        bbox=grid.bbox,
        n_rows=len(groups),
        n_cols=grid.n_cols,
        header_rows=min(grid.header_rows, len(groups)),
        cells=tuple(cells),
        source_engine=grid.source_engine,
        confidence=grid.confidence,
    )


def _key_column(grid: TableGrid) -> int | None:
    """行 key 列：默认首列；首列全空时退到最左侧有内容的列。"""
    filled: set[int] = {cell.col for cell in grid.cells if cell.text.strip() and cell.colspan == 1}
    if 0 in filled:
        return 0
    return min(filled) if filled else None


def _span_covered(grid: TableGrid) -> set[tuple[int, int]]:
    """被上方单元格 rowspan 覆盖的格子集合。"""
    covered: set[tuple[int, int]] = set()
    for cell in grid.cells:
        if cell.rowspan <= 1:
            continue
        for row in range(cell.row + 1, cell.row + cell.rowspan):
            for col in range(cell.col, cell.col + cell.colspan):
                covered.add((row, col))
    return covered


def _group_rows(
    grid: TableGrid,
    key_col: int,
    starts: dict[tuple[int, int], TableCell],
    covered: set[tuple[int, int]],
) -> list[list[int]]:
    groups: list[list[int]] = []
    current: list[int] = []
    for row in range(grid.n_rows):
        starts_new = (
            row == 0
            or _starts_record(row, key_col, starts, covered, grid.n_cols)
            or _row_has_colspan(row, starts, grid.n_cols)
            or _group_has_colspan(current, starts, grid.n_cols)
        )
        if starts_new:
            if current:
                groups.append(current)
            current = [row]
        else:
            current.append(row)
    if current:
        groups.append(current)
    return groups


def _row_has_colspan(
    row: int, starts: dict[tuple[int, int], TableCell], n_cols: int
) -> bool:
    return any(
        (cell := starts.get((row, col))) is not None and cell.colspan > 1
        for col in range(n_cols)
    )


def _group_has_colspan(
    group: list[int], starts: dict[tuple[int, int], TableCell], n_cols: int
) -> bool:
    return any(
        _row_has_colspan(row, starts, n_cols) for row in group
    )


def _starts_record(
    row: int,
    key_col: int,
    starts: dict[tuple[int, int], TableCell],
    covered: set[tuple[int, int]],
    n_cols: int,
) -> bool:
    key = starts.get((row, key_col))
    if key is not None and key.text.strip():
        return True
    return not any((row, col) in covered for col in range(n_cols))


def _buckets(
    group: list[int],
    starts: dict[tuple[int, int], TableCell],
    grid: TableGrid,
) -> dict[int, tuple[list[str], int]]:
    """把一个逻辑行内各物理行的单元格按列归拢，文本逐行拼接。"""
    buckets: dict[int, tuple[list[str], int]] = {}
    for row in group:
        for col in range(grid.n_cols):
            cell = starts.get((row, col))
            if cell is None:
                continue
            texts, colspan = buckets.setdefault(col, ([], cell.colspan))
            value = cell.text.strip()
            if value:
                texts.append(value)
    return buckets
