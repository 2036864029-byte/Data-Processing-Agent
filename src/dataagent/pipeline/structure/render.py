"""PR-1 Markdown 视图渲染：块序列 → 契约第三版交付 Markdown。"""

from __future__ import annotations

from collections.abc import Mapping

from dataagent.pipeline.structure.models import Block, DocumentBlocks, TableSlot
from dataagent.pipeline.table.html import to_html


def render_document_markdown(
    blocks: DocumentBlocks, *, fallback: Mapping[int, str]
) -> str:
    """按页渲染块序列；无块的页（缺口页等）回退到原始页 markdown。"""
    parts: list[str] = []
    for page in blocks.pages:
        if not page.blocks:
            text = fallback.get(page.page_no, "").rstrip("\n")
            if text:
                parts.append(text)
            continue
        parts.append("\n\n".join(_render_block(block) for block in page.blocks))
    if not parts:
        return ""
    return "\n".join(parts) + "\n"


def _render_block(block: Block) -> str:
    if block.kind == "heading":
        level = block.level if block.level is not None else 1
        return f"{'#' * level} {block.text}"
    if block.kind == "table" and block.table is not None:
        return _render_table(block.table)
    return block.text


def _render_table(slot: TableSlot) -> str:
    table_id = slot.table_id or ""
    caption = (slot.caption or "").strip()
    if table_id and table_id not in caption:
        visible = f"{caption}（{table_id}）" if caption else f"（{table_id}）"
    else:
        visible = caption or f"（{table_id}）"
    return f"{visible}\n<!--table:{table_id}-->\n{to_html(slot.grid)}"
