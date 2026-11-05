"""基于坐标的表格网格构建（L1）。

输入 pdf-inspector 的文本项（文字 + bbox），输出 `TableGrid`：
- 行聚类：按垂直中心与文本高度分组；
- 单元格：同一行内按水平间隙合并文本项；
- 列对齐：跨行聚类单元格起始 x，按覆盖列数生成 colspan；
- 区域检测：连续多列行组成候选表格区域，排除普通正文行。

不依赖新的解析引擎；只在需要表格重建的页调用。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.quality.text_normalize import normalize_cjk_text

ROW_TOLERANCE_RATIO = 1.0
CELL_GAP_TOLERANCE = 3.0
MIN_CELL_GAP = 8.0
DEFAULT_MIN_ROWS = 2
DEFAULT_MIN_COLS = 2


@dataclass(frozen=True, slots=True)
class PositionedText:
    text: str
    x: float
    y: float
    width: float
    height: float
    mcid: int | None = None

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


@dataclass(frozen=True, slots=True)
class TableRegion:
    page_no: int
    bbox: tuple[float, float, float, float]
    items: tuple[PositionedText, ...]
    confidence: float


@dataclass
class _CellGroup:
    items: list[PositionedText] = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0

    @property
    def text(self) -> str:
        joined = "".join(item.text for item in self.items)
        return normalize_cjk_text(joined).strip()


@dataclass
class _Row:
    center: float
    tolerance: float
    items: list[PositionedText] = field(default_factory=list)
    cells: list[_CellGroup] = field(default_factory=list)


def from_pdf_inspector(item: Any) -> PositionedText:
    return PositionedText(
        text=str(item.text or ""),
        x=float(item.x),
        y=float(item.y),
        width=float(item.width),
        height=float(item.height),
        mcid=None if getattr(item, "mcid", None) is None else int(item.mcid),
    )


def items_for_page(items: Iterable[Any], page_no: int) -> list[PositionedText]:
    """筛选某页文本项；pdf-inspector 的 item.page 是 1-indexed。"""
    return [
        from_pdf_inspector(item)
        for item in items
        if int(item.page) == page_no
    ]


def detect_table_regions(
    items: Sequence[PositionedText],
    *,
    page_no: int,
    min_rows: int = DEFAULT_MIN_ROWS,
    min_cols: int = DEFAULT_MIN_COLS,
) -> list[TableRegion]:
    """检测连续多列行组成的表格区域（保守；正文行不入选）。"""
    rows = _cluster_rows(items)
    flags = [_is_candidate_row(row, min_cols) for row in rows]
    groups: list[list[_Row]] = []
    run: list[_Row] = []
    index = 0
    while index < len(rows):
        if flags[index]:
            if run and _vertical_gap(run[-1], rows[index]) > _gap_limit(run):
                if len(run) >= min_rows:
                    groups.append(run)
                run = []
            run.append(rows[index])
            index += 1
            continue
        # 允许最多 1 个非候选行夹在候选行之间（合并单元格/跨行文本）
        if (
            run
            and index + 1 < len(rows)
            and flags[index + 1]
            and _row_within_run(rows[index], run)
        ):
            run.append(rows[index])
            index += 1
            continue
        if len(run) >= min_rows:
            groups.append(run)
        run = []
        index += 1
    if len(run) >= min_rows:
        groups.append(run)
    groups = _merge_adjacent_groups(groups)
    return [
        region
        for region in (
            _region_from_rows(group, page_no, min_cols) for group in groups
        )
        if region.confidence > 0
    ]


def _merge_adjacent_groups(groups: list[list[_Row]]) -> list[list[_Row]]:
    """垂直间隔小的相邻块合并：避免一张表被合并单元格行切成多段。"""
    merged: list[list[_Row]] = []
    for group in groups:
        if not group:
            continue
        if merged:
            previous = merged[-1]
            gap = _vertical_gap(previous[-1], group[0])
            if 0 < gap <= _gap_limit(previous):
                merged[-1] = [*previous, *group]
                continue
        merged.append(list(group))
    return merged


def _row_within_run(row: _Row, run: Sequence[_Row]) -> bool:
    left = min(item.x for item in row.items)
    right = max(item.right for item in row.items)
    run_left = min(item.x for value in run for item in value.items)
    run_right = max(item.right for value in run for item in value.items)
    return left >= run_left - 10 and right <= run_right + 10


def _vertical_gap(previous: _Row, current: _Row) -> float:
    return previous.center - current.center


def _gap_limit(rows: Sequence[_Row]) -> float:
    heights = [item.height for row in rows for item in row.items]
    median_height = sorted(heights)[len(heights) // 2] if heights else 10.0
    return max(12.0, median_height * 1.8)


def build_table_grid(
    items: Sequence[PositionedText],
    *,
    page_no: int,
    bbox: tuple[float, float, float, float] | None = None,
    header_rows: int = 1,
    source_engine: str = "pdf-inspector.positions",
    confidence: float | None = None,
) -> TableGrid:
    """把区域内的文本项构建成 TableGrid。"""
    point = bbox or _union_bbox(items)
    selected = [
        item
        for item in items
        if _inside(item, point) and item.text.strip()
    ]
    if not selected:
        raise ValueError("表格区域内没有可用文本项")
    rows = _cluster_rows(selected)
    for row in rows:
        row.cells = _group_cells(row.items)
    columns = _column_starts(rows)
    if not columns:
        raise ValueError("无法确定表格列")
    cells: list[TableCell] = []
    for row_index, row in enumerate(rows):
        next_col = 0
        for cell in sorted(row.cells, key=lambda value: value.start):
            column = max(_nearest_column(cell, columns), next_col)
            if column >= len(columns):
                continue
            colspan = min(
                _covered_columns(cell, column, columns), len(columns) - column
            )
            next_col = column + colspan
            cells.append(
                TableCell(
                    row=row_index,
                    col=column,
                    text=cell.text,
                    colspan=colspan,
                    bbox=(cell.start, _row_bottom(row), cell.end, _row_top(row)),
                )
            )
    consistency = _column_consistency(rows, len(columns))
    return TableGrid(
        page_no=page_no,
        page_span=(page_no, page_no),
        bbox=point,
        n_rows=len(rows),
        n_cols=len(columns),
        header_rows=min(header_rows, len(rows)),
        cells=tuple(cells),
        source_engine=source_engine,
        confidence=(
            round(confidence, 4)
            if confidence is not None
            else round(min(1.0, 0.5 + 0.5 * consistency), 4)
        ),
    )


def _cluster_rows(items: Sequence[PositionedText]) -> list[_Row]:
    ordered = sorted(items, key=lambda item: (-item.center_y, item.x))
    rows: list[_Row] = []
    for item in ordered:
        tolerance = max(2.0, item.height * ROW_TOLERANCE_RATIO)
        target = next(
            (row for row in rows if abs(row.center - item.center_y) <= row.tolerance),
            None,
        )
        if target is None:
            rows.append(
                _Row(
                    center=item.center_y,
                    tolerance=tolerance,
                    items=[item],
                )
            )
        else:
            target.items.append(item)
    return sorted(rows, key=lambda row: -row.center)


def _group_cells(items: Sequence[PositionedText]) -> list[_CellGroup]:
    groups: list[_CellGroup] = []
    for item in sorted(items, key=lambda value: value.x):
        if groups and item.x - groups[-1].end < CELL_GAP_TOLERANCE:
            group = groups[-1]
            group.items.append(item)
            group.end = max(group.end, item.right)
            continue
        groups.append(_CellGroup(items=[item], start=item.x, end=item.right))
    return groups


def _is_candidate_row(row: _Row, min_cols: int) -> bool:
    cells = _group_cells(row.items)
    if len(cells) < min_cols:
        return False
    gaps = [cells[index + 1].start - cells[index].end for index in range(len(cells) - 1)]
    return bool(gaps) and max(gaps) >= MIN_CELL_GAP


def _region_from_rows(
    rows: Sequence[_Row], page_no: int, min_cols: int
) -> TableRegion:
    items = tuple(item for row in rows for item in row.items)
    counts = [len(_group_cells(row.items)) for row in rows]
    mode = max(set(counts), key=counts.count)
    consistency = sum(1 for count in counts if count == mode) / len(counts)
    confidence = consistency if mode >= min_cols else 0.0
    return TableRegion(
        page_no=page_no,
        bbox=_union_bbox(items),
        items=items,
        confidence=round(confidence, 4),
    )


COLUMN_TOLERANCE = 12.0


def _column_starts(rows: Sequence[_Row]) -> list[float]:
    """用列数最多的行提取列锚，避免跨列宽单元格把两列合并。"""
    counts = [len(row.cells) for row in rows if row.cells]
    if not counts:
        return []
    target = max(counts)
    candidates = [row for row in rows if len(row.cells) == target]
    widest = max(
        candidates,
        key=lambda row: max(cell.end for cell in row.cells)
        - min(cell.start for cell in row.cells),
    )
    starts = [cell.start for cell in widest.cells]
    columns: list[float] = []
    for start in starts:
        if not columns or start - columns[-1] > COLUMN_TOLERANCE:
            columns.append(start)
        else:
            columns[-1] = min(columns[-1], start)
    return columns


def _nearest_column(cell: _CellGroup, columns: Sequence[float]) -> int:
    return min(
        range(len(columns)),
        key=lambda index: abs(columns[index] - cell.start),
    )


def _covered_columns(
    cell: _CellGroup, column: int, columns: Sequence[float]
) -> int:
    span = 1
    for next_start in columns[column + 1 :]:
        if next_start <= cell.end + 1.0:
            span += 1
        else:
            break
    return span


def _column_consistency(rows: Sequence[_Row], n_cols: int) -> float:
    if not rows:
        return 0.0
    aligned = sum(1 for row in rows if len(row.cells) >= min(n_cols, 2))
    return aligned / len(rows)


def _union_bbox(
    items: Sequence[PositionedText],
) -> tuple[float, float, float, float]:
    left = min(item.x for item in items)
    right = max(item.right for item in items)
    bottom = min(item.y for item in items)
    top = max(item.y + item.height for item in items)
    return (left, bottom, right, top)


def _inside(
    item: PositionedText, bbox: tuple[float, float, float, float]
) -> bool:
    x0, y0, x1, y1 = bbox
    center_x = item.x + item.width / 2
    center_y = item.center_y
    return x0 <= center_x <= x1 and y0 <= center_y <= y1


def _row_bottom(row: _Row) -> float:
    return min(item.y for item in row.items)


def _row_top(row: _Row) -> float:
    return max(item.y + item.height for item in row.items)
