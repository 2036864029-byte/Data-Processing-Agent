"""pdfplumber 有框线表格 → TableGrid（L2 表格抽取适配器）。

只负责“有框线表格”的确定性抽取：
- 用 pdfplumber 的 lines 策略读取真实单元格边界（lines + rects 均视为边）；
- 把 pdfplumber 的左上原点坐标转换为 contracts/table.py 的左下原点坐标；
- 合并单元格按 bbox 跨过的行/列边界推断 rowspan/colspan；
- 退化表（<2 行、<2 列、非空格 <2）直接丢弃。

不做表格质量判断与路由；验收网关在 pipeline/table/acceptance.py。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Self

import pdfplumber

from dataagent.contracts.table import TableCell, TableGrid

TABLE_SETTINGS: dict[str, str] = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
}
MIN_ROWS = 2
MIN_COLS = 2
MIN_CELLS = 2
EDGE_TOLERANCE = 0.5


class PdfplumberTableSource:
    """按页抽取有框线表格；一个实例对应一份文档。"""

    def __init__(self, source: str | Path | bytes) -> None:
        self._stream: io.BytesIO | None = None
        if isinstance(source, bytes):
            self._stream = io.BytesIO(source)
            self._pdf = pdfplumber.open(self._stream)
        else:
            self._pdf = pdfplumber.open(str(source))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._pdf.close()
        if self._stream is not None:
            self._stream.close()

    def extract_page(self, page_no: int) -> tuple[TableGrid, ...]:
        if page_no < 1 or page_no > len(self._pdf.pages):
            return ()
        page = self._pdf.pages[page_no - 1]
        try:
            tables = page.find_tables(table_settings=TABLE_SETTINGS)
            grids = (
                grid_from_table(
                    table, page_no=page_no, page_height=float(page.height)
                )
                for table in tables
            )
            return tuple(grid for grid in grids if grid is not None)
        finally:
            flush = getattr(page, "flush_cache", None)
            if callable(flush):
                flush()


def grid_from_table(
    table: Any, *, page_no: int, page_height: float
) -> TableGrid | None:
    """把单个 pdfplumber Table 转为 TableGrid；退化表返回 None。"""
    raw_cells = [tuple(float(value) for value in cell) for cell in table.cells]
    if not raw_cells:
        return None
    xs = _cluster_edges(
        [cell[0] for cell in raw_cells] + [cell[2] for cell in raw_cells]
    )
    ys = _cluster_edges(
        [cell[1] for cell in raw_cells] + [cell[3] for cell in raw_cells]
    )
    n_rows = len(ys) - 1
    n_cols = len(xs) - 1
    if n_rows < MIN_ROWS or n_cols < MIN_COLS:
        return None

    matrix = table.extract()
    cells: list[TableCell] = []
    for x0, top, x1, bottom in raw_cells:
        row = _edge_index(ys, top)
        row_end = _edge_index(ys, bottom)
        col = _edge_index(xs, x0)
        col_end = _edge_index(xs, x1)
        if row is None or row_end is None or col is None or col_end is None:
            continue
        if row_end <= row or col_end <= col:
            continue
        text = _matrix_text(matrix, row, col)
        if not text:
            continue
        cells.append(
            TableCell(
                row=row,
                col=col,
                text=text,
                rowspan=row_end - row,
                colspan=col_end - col,
                bbox=_convert_bbox((x0, top, x1, bottom), page_height),
            )
        )
    if len(cells) < MIN_CELLS:
        return None

    bbox_x0, bbox_top, bbox_x1, bbox_bottom = (
        float(value) for value in table.bbox
    )
    bbox = _convert_bbox(
        (bbox_x0, bbox_top, bbox_x1, bbox_bottom), page_height
    )
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        return None
    covered = sum(cell.rowspan * cell.colspan for cell in cells)
    coverage = min(1.0, covered / (n_rows * n_cols))
    header_rows = 1 if any(cell.row == 0 for cell in cells) else 0
    return TableGrid(
        page_no=page_no,
        page_span=(page_no, page_no),
        bbox=bbox,
        n_rows=n_rows,
        n_cols=n_cols,
        header_rows=header_rows,
        cells=tuple(cells),
        source_engine="pdfplumber.lines",
        confidence=round(0.5 + 0.5 * coverage, 4),
    )


def _cluster_edges(values: list[float]) -> list[float]:
    ordered = sorted(values)
    clusters: list[float] = []
    for value in ordered:
        if not clusters or value - clusters[-1] > EDGE_TOLERANCE:
            clusters.append(value)
    return clusters


def _edge_index(edges: list[float], value: float) -> int | None:
    nearest = min(range(len(edges)), key=lambda index: abs(edges[index] - value))
    if abs(edges[nearest] - value) > EDGE_TOLERANCE:
        return None
    return nearest


def _matrix_text(matrix: Any, row: int, col: int) -> str:
    try:
        value = matrix[row][col]
    except (IndexError, TypeError):
        return ""
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _convert_bbox(
    bbox: tuple[float, float, float, float], page_height: float
) -> tuple[float, float, float, float]:
    x0, top, x1, bottom = bbox
    return (x0, page_height - bottom, x1, page_height - top)
