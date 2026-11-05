"""网格级跨页表合并：把相邻页边界处的同一张表追加为单个 TableGrid。

触发条件沿用文本版：上页最后一格是表、本页第一格是表、列数一致、表头相似 ≥0.8。
合并后 `page_no` 记起始页，`page_span` 记（起始页, 结束页），`bbox` 取起始页。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from difflib import SequenceMatcher

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.structure.models import Block, PageBlocks

HEADER_SIMILARITY_MIN = 0.8


def merge_cross_page_tables(
    pages: Sequence[PageBlocks],
) -> tuple[PageBlocks, ...]:
    result = list(pages)
    pending_index: int | None = None
    pending_block: Block | None = None

    for index, page in enumerate(result):
        if page.gap:
            pending_index = None
            pending_block = None
            continue
        if not page.blocks:
            continue
        first = page.blocks[0]
        if (
            pending_index is not None
            and pending_block is not None
            and pending_block.table is not None
            and first.kind == "table"
            and first.table is not None
        ):
            merged = _append_grid(pending_block.table.grid, first.table.grid)
            if merged is not None:
                slot = replace(pending_block.table, grid=merged)
                block = replace(pending_block, table=slot)
                base = result[pending_index]
                result[pending_index] = replace(
                    base,
                    blocks=tuple(
                        block if item is pending_block else item
                        for item in base.blocks
                    ),
                )
                pending_block = block
                remaining = page.blocks[1:]
                result[index] = replace(page, blocks=remaining)
                if remaining:
                    if remaining[-1].kind == "table":
                        pending_index = index
                        pending_block = remaining[-1]
                    else:
                        pending_index = None
                        pending_block = None
                continue

        if page.blocks[-1].kind == "table":
            pending_index = index
            pending_block = page.blocks[-1]
        else:
            pending_index = None
            pending_block = None

    return tuple(result)


def _append_grid(base: TableGrid, current: TableGrid) -> TableGrid | None:
    if base.n_cols != current.n_cols:
        return None
    base_header = _header_texts(base)
    current_header = _header_texts(current)
    if not _similar(base_header, current_header):
        return None
    skip = current.header_rows
    if skip == 0:
        skip = 1
    data_rows = current.n_rows - skip
    if data_rows <= 0:
        return None

    cells = list(base.cells)
    for cell in current.cells:
        if cell.row < skip:
            continue
        cells.append(
            cell.model_copy(
                update={"row": cell.row - skip + base.n_rows}
            )
        )
    try:
        return TableGrid(
            page_no=base.page_no,
            page_span=(base.page_span[0], current.page_span[1]),
            bbox=base.bbox,
            n_rows=base.n_rows + data_rows,
            n_cols=base.n_cols,
            header_rows=base.header_rows,
            cells=tuple(cells),
            source_engine=base.source_engine,
            confidence=base.confidence,
        )
    except ValueError:
        return None


def _header_texts(grid: TableGrid) -> list[str]:
    row = sorted(
        (cell for cell in grid.cells if cell.row == 0),
        key=lambda cell: cell.col,
    )
    return [cell.text.casefold() for cell in row]


def _similar(left: Sequence[str], right: Sequence[str]) -> bool:
    if not left or not right:
        return False
    ratio = SequenceMatcher(None, "|".join(left), "|".join(right)).ratio()
    return ratio >= HEADER_SIMILARITY_MIN
