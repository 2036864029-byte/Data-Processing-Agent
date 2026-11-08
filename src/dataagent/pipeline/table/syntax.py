"""Markdown 表格语法（叶子模块，无内部依赖）。

只放被多处复用的语法判定，避免 `table.markdown`（依赖 quality.normalization）
成为 repair / quality / structure 的公共依赖而触发循环导入。
"""

from __future__ import annotations

import re
from typing import Final

SEPARATOR_CELL: Final[re.Pattern[str]] = re.compile(r"^:?-{3,}:?$")


def is_separator_cell(text: str) -> bool:
    """Markdown 表格分隔单元格（`---` / `:--:`）。"""
    return SEPARATOR_CELL.fullmatch(text.strip()) is not None
