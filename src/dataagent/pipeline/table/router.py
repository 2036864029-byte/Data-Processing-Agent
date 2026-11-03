"""表格路由：L0 Markdown 表 vs L1 坐标网格，按质量择优。

- L0 快但可能丢失表头/列结构；
- L1 基于文本坐标，适合无边框表，但可能受区域检测影响；
- 逐表配对（按出现顺序），质量更高者入选；平手优先 L0（更快、更稳）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.table.geometry import (
    PositionedText,
    build_table_grid,
    detect_table_regions,
)
from dataagent.pipeline.table.markdown import markdown_tables
from dataagent.pipeline.table.quality import GridQuality, grid_quality

SELECTION_MARGIN = 0.05


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
    bbox = _page_bbox(items)
    l0 = markdown_tables(markdown, page_no=page_no, bbox=bbox)
    l1_pairs = []
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

    choices: list[TableChoice] = []
    pairs = min(len(l0), len(l1_pairs))
    for index in range(pairs):
        markdown_grid = l0[index]
        geometry_pair = l1_pairs[index]
        geometry_grid = geometry_pair[0].model_copy(
            update={"bbox": geometry_pair[1]}
        )
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

    for grid in l0[pairs:]:
        choices.append(
            TableChoice(
                grid=grid,
                source="markdown",
                reason="only_l0",
                quality=grid_quality(grid),
            )
        )
    # 有 L0 时，多余 L1 区域通常是同一张表被切碎的片段，不重复输出；
    # 无 L0 时才把每个 L1 区域当作独立表。
    if not l0:
        for grid, _bbox in l1_pairs[pairs:]:
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


def _page_bbox(items: Sequence[PositionedText]) -> tuple[float, float, float, float]:
    if not items:
        return (0.0, 0.0, 1.0, 1.0)
    return (
        min(item.x for item in items),
        min(item.y for item in items),
        max(item.x + item.width for item in items),
        max(item.y + item.height for item in items),
    )
