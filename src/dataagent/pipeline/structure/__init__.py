"""PR-1 结构建块：统一行聚类 + 表格槽位 + bbox 安全阀。"""

from __future__ import annotations

from .build import build_document_blocks, build_page_blocks
from .models import (
    Block,
    DocumentBlocks,
    PageBlocks,
    PageBuildResult,
    PageLayout,
    TableSlot,
)

__all__ = [
    "Block",
    "DocumentBlocks",
    "PageBlocks",
    "PageBuildResult",
    "PageLayout",
    "TableSlot",
    "build_document_blocks",
    "build_page_blocks",
]
