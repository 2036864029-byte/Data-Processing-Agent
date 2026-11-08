"""表格槽位：路由结果 → 页面表格槽位，以及 caption 归属。

槽位是建块的边界：它决定哪些 Markdown 行被表格替换、哪些表被降级成正文。
归一化（单元格文本 + 逻辑行合并）与伪表降级都收在这一个汇聚点，
保证 md / json / tables.json / HTML 看到的是同一个网格。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.quality.normalization import normalize_table_grid
from dataagent.pipeline.structure.lines import Line
from dataagent.pipeline.structure.models import BBox, TableSlot
from dataagent.pipeline.table import bbox as bbox_math
from dataagent.pipeline.table.geometry import PositionedText
from dataagent.pipeline.table.policy import (
    DEFAULT_TABLE_POLICY,
    TablePolicy,
    TableSource,
)
from dataagent.pipeline.table.prose import is_prose_cells
from dataagent.pipeline.table.router import TableRouting
from dataagent.pipeline.table.rowmerge import merge_logical_rows
from dataagent.platform.observe.log import log_event

BBOX_TRUST_AREA_RATIO: Final[float] = 0.6
SLOT_OVERLAP_RATIO: Final[float] = 0.15
CAPTION_MAX_CHARS: Final[int] = 60
CAPTION_SEARCH_DEPTH: Final[int] = 3

_CAPTION_RE: Final[re.Pattern[str]] = re.compile(r"表\s*\d+[-—.]?\d*")


def build_slots(
    routing: TableRouting,
    l0_grids: Sequence[TableGrid],
    spans: Sequence[tuple[int, int]],
    page_bbox: BBox,
    *,
    page_no: int,
    has_items: bool,
    policy: TablePolicy = DEFAULT_TABLE_POLICY,
) -> tuple[list[TableSlot], list[tuple[int, int]], list[str]]:
    slots: list[TableSlot] = []
    used_l0: set[int] = set()
    for choice in routing.tables:
        index = match_l0(choice.grid, l0_grids, used_l0)
        span = None
        if index is not None:
            used_l0.add(index)
            span = spans[index] if index < len(spans) else None
        slots.append(
            TableSlot(
                grid=choice.grid,
                bbox=choice.grid.bbox,
                source=choice.source,
                trusted_bbox=trusted_bbox(choice.grid.bbox, page_bbox),
                span=span,
            )
        )
    for index, grid in enumerate(l0_grids):
        if index in used_l0:
            continue
        slots.append(
            TableSlot(
                grid=grid,
                bbox=grid.bbox,
                source=TableSource.MARKDOWN,
                trusted_bbox=trusted_bbox(grid.bbox, page_bbox),
                span=spans[index] if index < len(spans) else None,
            )
        )
    demoted: list[tuple[int, int]] = []
    # 先做单元格文本归一，再做逻辑行合并：合并会把逻辑行用 \n 拼进同一单元格，
    # 之后再归一就会把逻辑行边界并掉。
    slots = [
        replace(slot, grid=merge_logical_rows(normalize_table_grid(slot.grid)))
        for slot in slots
    ]
    # 有坐标项时同样要拦 L0 伪表格：否则整页正文会被假表吞掉，
    # 坐标项也不会再进入文本块（R-0033 / R-0036 实测）。
    kept: list[TableSlot] = []
    for slot in slots:
        if slot.span is not None and is_prose_table(slot, page_bbox, policy):
            demoted.append(slot.span)
        else:
            kept.append(slot)
    slots = kept
    warnings: list[str] = []
    if has_items and any(not slot.trusted_bbox for slot in slots):
        warnings.append(f"table_bbox_untrusted:{page_no}")
    if demoted:
        warnings.append(f"table_l0_prose_demoted:{page_no}")
        log_event(
            "table_l0_prose_demoted",
            page_no=page_no,
            spans=len(demoted),
        )
    return slots, demoted, warnings


def match_l0(
    grid: TableGrid, l0_grids: Sequence[TableGrid], used: set[int]
) -> int | None:
    signature = grid_signature(grid)
    for index, l0 in enumerate(l0_grids):
        if index not in used and grid_signature(l0) == signature:
            return index
    best: tuple[float, int] | None = None
    for index, l0 in enumerate(l0_grids):
        if index in used:
            continue
        ratio = bbox_math.symmetric_overlap_ratio(grid.bbox, l0.bbox)
        if ratio >= SLOT_OVERLAP_RATIO and (best is None or ratio > best[0]):
            best = (ratio, index)
    return None if best is None else best[1]


def grid_signature(grid: TableGrid) -> tuple[tuple[int, int, str, int, int], ...]:
    return tuple(
        sorted(
            (cell.row, cell.col, cell.text, cell.rowspan, cell.colspan)
            for cell in grid.cells
        )
    )


def trusted_bbox(bbox: BBox, page_bbox: BBox) -> bool:
    page_area = bbox_math.area(page_bbox)
    if page_area <= 0:
        return False
    return bbox_math.area(bbox) < page_area * BBOX_TRUST_AREA_RATIO and bbox != page_bbox


def is_prose_table(
    slot: TableSlot, page_bbox: BBox, policy: TablePolicy = DEFAULT_TABLE_POLICY
) -> bool:
    """L0 表若整体像正文而不是行列结构，视为 pdf-inspector 伪表格。

    旧逻辑只看首行，且不要求表格覆盖页面大部分区域；实测会把
    “页眉 + 整页正文”或多行问答误判成表（R-0033 / R-0036）。
    新逻辑：表格 bbox 覆盖页面 ≥60%，且出现 ≥2 个带句末标点的散文单元格，
    或任一 ≥120 字的散文单元格。
    """
    if slot.source != TableSource.MARKDOWN:
        return False
    grid = slot.grid
    page_area = bbox_math.area(page_bbox)
    if page_area <= 0:
        return False
    if bbox_math.area(grid.bbox) / page_area < policy.prose_table_area_ratio:
        return False
    return is_prose_cells((cell.text for cell in grid.cells), policy=policy)


def inside_any(item: PositionedText, slots: Sequence[TableSlot]) -> bool:
    """坐标项中心是否落在任一表格槽位内。"""
    x = item.x + item.width / 2
    y = item.y + item.height / 2
    return any(
        slot.bbox[0] <= x <= slot.bbox[2] and slot.bbox[1] <= y <= slot.bbox[3]
        for slot in slots
    )


def is_caption_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > CAPTION_MAX_CHARS:
        return False
    if stripped.startswith("#"):
        return False
    return _CAPTION_RE.search(stripped) is not None


def markdown_captions(
    lines: Sequence[str], slots: Sequence[TableSlot]
) -> tuple[list[TableSlot], set[int]]:
    consumed: set[int] = set()
    updated: list[TableSlot] = []
    for slot in slots:
        if slot.span is None:
            updated.append(slot)
            continue
        found = caption_above_lines(lines, slot.span[0])
        if found is not None:
            caption, index = found
            consumed.add(index)
            slot = replace(slot, caption=caption)
        updated.append(slot)
    return updated, consumed


def caption_above_lines(
    lines: Sequence[str], start: int
) -> tuple[str, int] | None:
    for offset in range(1, CAPTION_SEARCH_DEPTH + 1):
        index = start - offset
        if index < 0:
            return None
        text = lines[index].strip()
        if not text:
            continue
        return (text, index) if is_caption_text(text) else None
    return None


def line_captions(
    lines: Sequence[Line], slots: Sequence[TableSlot]
) -> tuple[list[Line], set[str], list[TableSlot]]:
    consumed: set[str] = set()
    updated: list[TableSlot] = []
    for slot in slots:
        found = caption_above_table(lines, slot.bbox)
        if found is not None:
            consumed.add(found.norm)
            slot = replace(slot, caption=found.text.strip())
        updated.append(slot)
    return list(lines), consumed, updated


def caption_above_table(
    lines: Sequence[Line], bbox: BBox
) -> Line | None:
    top = bbox[3]
    candidates = [
        line
        for line in lines
        if line.center > top
        and line.center - top <= max(20.0, line.height * 1.6)
        and is_caption_text(line.text)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda line: line.center - top)
