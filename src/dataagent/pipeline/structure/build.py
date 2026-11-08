"""PR-1 建块编排：页面级与文档级流程。

拆分后的职责边界（Phase 5）：
- `structure.lines`：坐标项行聚类与行内数字回挂；
- `structure.slots`：表格槽位、归一化汇聚点、伪表降级、caption 归属；
- `structure.blocks`：两条建块路径（Markdown 回退 / 坐标项）；
- `table.bbox`：BBox 几何工具（全仓唯一实现）。

决策依据（2026-11-05 原型）：
- 结构元素可作为标题角色来源，但 P 粒度是逐文本 run（茅台 p25：147 项 / 381 个 P），
  不能当段落边界；因此 tagged/untagged 统一用「坐标项行聚类 + 段落合并」。
- 标题层级以 Markdown 的 `#` 个数为准，文档级归一化在 `structure/headings.py`。

无坐标项页面（非表格页、CSV/text、text_layer_recovered、纯扫描）走 Markdown 块回退，
bbox 取页框；表格仍按行范围替换为 TableGrid。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.merge.merger import MergedDocument
from dataagent.pipeline.structure.blocks import (
    item_blocks,
    markdown_blocks,
    running_headers,
    table_id,
)
from dataagent.pipeline.structure.crosspage import merge_cross_page_tables
from dataagent.pipeline.structure.models import (
    Block,
    DocumentBlocks,
    PageBlocks,
    PageBuildResult,
    PageLayout,
)
from dataagent.pipeline.structure.slots import build_slots
from dataagent.pipeline.table import bbox as bbox_math
from dataagent.pipeline.table.markdown import markdown_table_spans, markdown_tables
from dataagent.pipeline.table.policy import DEFAULT_TABLE_POLICY, TablePolicy
from dataagent.pipeline.table.router import route_tables


def build_document_blocks(
    document: MergedDocument,
    *,
    layouts: Mapping[int, PageLayout],
    ruled_tables: Callable[[int], Sequence[TableGrid]] | None = None,
    policy: TablePolicy = DEFAULT_TABLE_POLICY,
) -> DocumentBlocks:
    pages: list[PageBlocks] = []
    warnings: list[str] = []
    dropped_lines = running_headers(document.pages)
    for page in document.pages:
        if page.gap:
            pages.append(PageBlocks(page_no=page.page_no, gap=True))
            continue
        layout = layouts.get(page.page_no, PageLayout(page_no=page.page_no))
        ruled = tuple(ruled_tables(page.page_no)) if ruled_tables is not None else ()
        result = build_page_blocks(
            page_no=page.page_no,
            markdown=page.markdown,
            layout=layout,
            ruled_tables=ruled,
            dropped_lines=dropped_lines,
            policy=policy,
        )
        pages.append(result.page)
        warnings.extend(result.warnings)

    merged = merge_cross_page_tables(tuple(pages))
    table_index = 0
    assigned_pages: list[PageBlocks] = []
    for merged_page in merged:
        assigned: list[Block] = []
        for block in merged_page.blocks:
            if block.kind == "table" and block.table is not None:
                table_index += 1
                slot = replace(block.table, table_id=table_id(table_index))
                block = replace(block, table=slot)
            assigned.append(block)
        assigned_pages.append(replace(merged_page, blocks=tuple(assigned)))
    return DocumentBlocks(
        doc_id=document.doc_id,
        pages=tuple(assigned_pages),
        warnings=tuple(warnings),
    )


def build_page_blocks(
    *,
    page_no: int,
    markdown: str,
    layout: PageLayout,
    ruled_tables: Sequence[TableGrid] = (),
    dropped_lines: frozenset[str] = frozenset(),
    policy: TablePolicy = DEFAULT_TABLE_POLICY,
) -> PageBuildResult:
    page_bbox = bbox_math.page_bbox(layout.items)
    l0_grids = markdown_tables(markdown, page_no=page_no, bbox=page_bbox)
    spans = markdown_table_spans(markdown)
    routing = route_tables(
        markdown,
        layout.items,
        page_no=page_no,
        ruled_tables=ruled_tables,
        policy=policy,
    )
    slots, demoted, warnings = build_slots(
        routing,
        l0_grids,
        spans,
        page_bbox,
        page_no=page_no,
        has_items=bool(layout.items),
        policy=policy,
    )
    if layout.items and all(slot.trusted_bbox for slot in slots):
        blocks, item_warnings = item_blocks(
            page_no=page_no,
            markdown=markdown,
            items=layout.items,
            slots=slots,
            page_bbox=page_bbox,
            roles=dict(layout.roles),
            dropped_lines=dropped_lines,
        )
        warnings.extend(item_warnings)
    else:
        blocks = markdown_blocks(
            page_no=page_no,
            markdown=markdown,
            slots=slots,
            page_bbox=page_bbox,
            demoted_spans=demoted,
            dropped_lines=dropped_lines,
        )
    return PageBuildResult(
        page=PageBlocks(page_no=page_no, blocks=tuple(blocks)),
        warnings=tuple(warnings),
    )
