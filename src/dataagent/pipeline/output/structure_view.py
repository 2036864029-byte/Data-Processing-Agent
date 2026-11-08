"""结构视图：内部块 → DocumentStructure；md / {meta, structure} JSON 同源渲染。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from dataagent.contracts.structure import (
    DocumentStructure,
    PageStructure,
    TableBlock,
    TextBlock,
)
from dataagent.contracts.table import TableGrid
from dataagent.pipeline.merge.merger import MergedDocument
from dataagent.pipeline.output.delivery import DeliveryDecision
from dataagent.pipeline.structure.headings import HeadingLevelNormalizer
from dataagent.pipeline.structure.models import DocumentBlocks
from dataagent.pipeline.table.html import to_html

CELL_SEPARATOR = "；"
HEADER_JOINER = "/"


def rows_text(grid: TableGrid) -> str:
    """表格行序列化：每行一条文本，行内按 `列名: 值` 拼接，行间换行。"""
    matrix = _expanded_matrix(grid)
    header_rows = min(grid.header_rows, grid.n_rows)
    names = _column_names(matrix, header_rows, grid.n_cols)
    lines: list[str] = []
    for row in range(header_rows, grid.n_rows):
        cells: list[str] = []
        for col in range(grid.n_cols):
            value = _one_line(matrix[row][col])
            if not value:
                continue
            name = names[col] or f"列{col + 1}"
            cells.append(f"{name}: {value}")
        if cells:
            lines.append(CELL_SEPARATOR.join(cells))
    return "\n".join(lines)


def build_structure(
    blocks: DocumentBlocks, *, doc_id: str
) -> DocumentStructure:
    total = sum(len(page.blocks) for page in blocks.pages)
    width = max(4, len(str(total))) if total else 4
    used_tables = {
        block.table.table_id
        for page in blocks.pages
        for block in page.blocks
        if block.kind == "table"
        and block.table is not None
        and block.table.table_id
    }
    next_table = 1

    def next_table_id() -> str:
        nonlocal next_table
        while f"t{next_table:03d}" in used_tables:
            next_table += 1
        table_id = f"t{next_table:03d}"
        used_tables.add(table_id)
        next_table += 1
        return table_id

    block_index = 0
    pages: list[PageStructure] = []
    headings = HeadingLevelNormalizer()
    for page in blocks.pages:
        if page.gap:
            continue
        page_blocks: list[TextBlock | TableBlock] = []
        for block in page.blocks:
            block_index += 1
            block_id = f"b{block_index:0{width}d}"
            if block.kind == "table" and block.table is not None:
                slot = block.table
                page_blocks.append(
                    TableBlock(
                        block_id=block_id,
                        page_no=page.page_no,
                        bbox=block.bbox,
                        grid=slot.grid,
                        table_id=slot.table_id or next_table_id(),
                        caption=slot.caption,
                        rows_text=rows_text(slot.grid),
                    )
                )
            else:
                level = block.level
                if block.kind == "heading":
                    level = headings.resolve(
                        block.text, level, page_no=page.page_no
                    )
                page_blocks.append(
                    TextBlock(
                        block_id=block_id,
                        page_no=page.page_no,
                        bbox=block.bbox,
                        text=block.text,
                        role="heading" if block.kind == "heading" else "body",
                        level=level,
                    )
                )
        pages.append(
            PageStructure(page_no=page.page_no, blocks=tuple(page_blocks))
        )
    return DocumentStructure(doc_id=doc_id, pages=tuple(pages))


def build_meta(
    document: MergedDocument, decision: DeliveryDecision
) -> dict[str, Any]:
    return {
        "doc_id": document.doc_id,
        "delivery_level": decision.level.value,
        "gap_ratio": round(decision.gap_ratio, 6),
        "requires_manual_review": decision.requires_manual_review,
        "validation": {
            "ok": document.validation.ok,
            "findings": [
                {
                    "code": finding.code,
                    "message": finding.message,
                    "page_no": finding.page_no,
                }
                for finding in document.validation.findings
            ],
        },
        "gaps": [
            {
                "page_no": page.page_no,
                "defect_codes": [code.value for code in page.defect_codes],
            }
            for page in document.pages
            if page.gap
        ],
    }


def to_json_view(
    structure: DocumentStructure, meta: Mapping[str, Any]
) -> str:
    payload = {
        "meta": dict(meta),
        "structure": structure.model_dump(mode="json"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def to_tables_json(
    structure: DocumentStructure | None, *, doc_id: str
) -> str:
    """可选第三产物：只含表格块的结构化 JSON。"""
    tables: list[dict[str, Any]] = []
    if structure is not None:
        for page in structure.pages:
            for block in page.blocks:
                if not isinstance(block, TableBlock):
                    continue
                tables.append(
                    {
                        "table_id": block.table_id,
                        "block_id": block.block_id,
                        "page_no": block.page_no,
                        "page_span": list(block.grid.page_span),
                        "bbox": list(block.bbox),
                        "caption": block.caption,
                        "rows_text": block.rows_text,
                        "grid": block.grid.model_dump(mode="json"),
                    }
                )
    payload = {"doc_id": doc_id, "tables": tables}
    return json.dumps(payload, ensure_ascii=False, indent=2)


def to_markdown_view(
    structure: DocumentStructure,
    *,
    page_order: Sequence[int],
    fallback: Mapping[int, str],
) -> str:
    pages = {page.page_no: page for page in structure.pages}
    parts: list[str] = []
    for page_no in page_order:
        page = pages.get(page_no)
        if page is None:
            text = fallback.get(page_no, "").rstrip("\n")
            if text:
                parts.append(text)
            continue
        if not page.blocks:
            continue
        parts.append(
            "\n\n".join(_render_block(block) for block in page.blocks)
        )
    if not parts:
        return ""
    return "\n".join(parts) + "\n"


def _render_block(block: TextBlock | TableBlock) -> str:
    if isinstance(block, TableBlock):
        return _render_table(block)
    if block.level is not None:
        return f"{'#' * block.level} {block.text}"
    return block.text


def _render_table(block: TableBlock) -> str:
    table_id = block.table_id
    caption = (block.caption or "").strip()
    if table_id and table_id not in caption:
        visible = f"{caption}（{table_id}）" if caption else f"（{table_id}）"
    else:
        visible = caption or f"（{table_id}）"
    return f"{visible}\n<!--table:{table_id}-->\n{to_html(block.grid)}"


def _expanded_matrix(grid: TableGrid) -> list[list[str]]:
    matrix = [["" for _ in range(grid.n_cols)] for _ in range(grid.n_rows)]
    for cell in grid.cells:
        for row in range(cell.row, cell.row + cell.rowspan):
            for col in range(cell.col, cell.col + cell.colspan):
                if row < grid.n_rows and col < grid.n_cols:
                    matrix[row][col] = cell.text
    return matrix


def _column_names(
    matrix: Sequence[Sequence[str]], header_rows: int, n_cols: int
) -> list[str]:
    if header_rows <= 0:
        return ["" for _ in range(n_cols)]
    names: list[str] = []
    for col in range(n_cols):
        parts: list[str] = []
        for row in range(header_rows):
            value = _one_line(matrix[row][col])
            if value and (not parts or parts[-1] != value):
                parts.append(value)
        names.append(HEADER_JOINER.join(parts))
    return names


def _one_line(text: str) -> str:
    """单元格内换行折叠为空格，保证一行表格对应一行 rows_text。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ").strip()
