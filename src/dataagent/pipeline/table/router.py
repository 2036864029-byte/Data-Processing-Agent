"""表格路由：L0 Markdown 表 vs L1 坐标网格，按空间重叠配对与质量择优。

- L0 通过锚点投影得到 bbox，才能与 L1 区域按空间位置配对（而不是按出现顺序）；
- 配对成功后按质量分择优；未配对的 L0 保留 Markdown；
- 未配对的 L1：若与任一 L0 区域重叠，视为碎片丢弃；否则作为独立表保留。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.table.anchor import ProjectedGrid, project_grid
from dataagent.pipeline.table.geometry import (
    PositionedText,
    build_table_grid,
    detect_table_regions,
)
from dataagent.pipeline.table.markdown import markdown_tables
from dataagent.pipeline.table.quality import GridQuality, grid_quality

SELECTION_MARGIN = 0.05
PAIR_MIN_OVERLAP = 0.15
FRAGMENT_OVERLAP = 0.5


@dataclass(frozen=True, slots=True)
class TableChoice:
    grid: TableGrid
    source: str
    reason: str
    quality: GridQuality


@dataclass(frozen=True, slots=True)
class TableRouting:
    tables: tuple[TableChoice, ...] = ()
    markdown_count: int = 0
    geometry_count: int = 0


def route_tables(
    markdown: str,
    items: Sequence[PositionedText],
    *,
    page_no: int,
) -> TableRouting:
    page_bbox = _page_bbox(items)
    l0 = markdown_tables(markdown, page_no=page_no, bbox=page_bbox)
    projected: list[ProjectedGrid | None] = [
        project_grid(grid, items) for grid in l0
    ]
    l0_grids: list[TableGrid] = []
    for index, grid in enumerate(l0):
        item = projected[index]
        l0_grids.append(item.grid if item is not None else grid)
    l1_pairs: list[tuple[TableGrid, tuple[float, float, float, float]]] = []
    for region in detect_table_regions(items, page_no=page_no):
        try:
            grid = build_table_grid(
                region.items,
                page_no=page_no,
                bbox=region.bbox,
                confidence=region.confidence,
            )
        except ValueError:
            continue
        l1_pairs.append((grid, region.bbox))

    pairs = _pair_by_overlap(l0_grids, projected, l1_pairs)
    paired_l0 = {index for index, _ in pairs}
    paired_l1 = {index for _, index in pairs}

    choices: list[TableChoice] = []
    for l0_index, l1_index in pairs:
        markdown_grid = l0_grids[l0_index]
        geometry_grid, bbox = l1_pairs[l1_index]
        geometry_grid = geometry_grid.model_copy(update={"bbox": bbox})
        markdown_quality = grid_quality(markdown_grid)
        geometry_quality = grid_quality(geometry_grid)
        if geometry_quality.score > markdown_quality.score + SELECTION_MARGIN:
            choices.append(
                TableChoice(
                    grid=geometry_grid,
                    source="geometry",
                    reason=(
                        f"geometry {geometry_quality.score:.3f} > "
                        f"markdown {markdown_quality.score:.3f}"
                    ),
                    quality=geometry_quality,
                )
            )
        else:
            choices.append(
                TableChoice(
                    grid=markdown_grid,
                    source="markdown",
                    reason=(
                        f"markdown {markdown_quality.score:.3f} >= "
                        f"geometry {geometry_quality.score:.3f}"
                    ),
                    quality=markdown_quality,
                )
            )

    for index, grid in enumerate(l0_grids):
        if index in paired_l0:
            continue
        choices.append(
            TableChoice(
                grid=grid,
                source="markdown",
                reason="only_l0",
                quality=grid_quality(grid),
            )
        )

    l0_boxes = [
        item.grid.bbox for item in projected if item is not None
    ]
    for index, (grid, bbox) in enumerate(l1_pairs):
        if index in paired_l1:
            continue
        if any(_overlap_ratio(bbox, box) >= FRAGMENT_OVERLAP for box in l0_boxes):
            continue
        choices.append(
            TableChoice(
                grid=grid,
                source="geometry",
                reason="only_l1",
                quality=grid_quality(grid),
            )
        )
    return TableRouting(
        tables=tuple(choices),
        markdown_count=len(l0),
        geometry_count=len(l1_pairs),
    )


def _pair_by_overlap(
    l0_grids: Sequence[TableGrid],
    projected: Sequence[ProjectedGrid | None],
    l1_pairs: Sequence[tuple[TableGrid, tuple[float, float, float, float]]],
) -> list[tuple[int, int]]:
    candidates: list[tuple[float, int, int]] = []
    for l1_index, (_grid, bbox) in enumerate(l1_pairs):
        for l0_index, item in enumerate(projected):
            if item is None:
                continue
            score = _intersection_area(item.grid.bbox, bbox)
            if score > 0:
                candidates.append((score, l0_index, l1_index))
    candidates.sort(reverse=True)
    used_l0: set[int] = set()
    used_l1: set[int] = set()
    pairs: list[tuple[int, int]] = []
    areas = {
        index: _area(item.grid.bbox)
        for index, item in enumerate(projected)
        if item is not None
    }
    for _score, l0_index, l1_index in candidates:
        if l0_index in used_l0 or l1_index in used_l1:
            continue
        # 按较小面积的重叠比例配对（L0 投影可能覆盖整页，包含关系也应配对）
        l0_area = areas.get(l0_index, 0.0)
        l1_area = _area(l1_pairs[l1_index][1])
        smaller = min(l0_area, l1_area)
        if smaller <= 0 or _score / smaller < PAIR_MIN_OVERLAP:
            continue
        used_l0.add(l0_index)
        used_l1.add(l1_index)
        pairs.append((l0_index, l1_index))
    return pairs


def _intersection_area(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    if x0 >= x1 or y0 >= y1:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _overlap_ratio(
    bbox: tuple[float, float, float, float],
    other: tuple[float, float, float, float],
) -> float:
    area = _area(bbox)
    if area <= 0:
        return 0.0
    return _intersection_area(bbox, other) / area


def _page_bbox(items: Sequence[PositionedText]) -> tuple[float, float, float, float]:
    if not items:
        return (0.0, 0.0, 1.0, 1.0)
    return (
        min(item.x for item in items),
        min(item.y for item in items),
        max(item.x + item.width for item in items),
        max(item.y + item.height for item in items),
    )
