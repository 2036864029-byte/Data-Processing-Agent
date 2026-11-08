"""伪表格（散文表）判定：L1 指标与建块共用同一判据。

pdf-inspector 会把“页眉 + 整页正文”或长问答题误判成表格（R-0002 / R-0033 / R-0036）。
两个阶段必须用同一判据，否则一个说“有表且行列结构坏”，另一个把它降级成正文，
页面就会被判成缺口，而交付里根本没有表。

判据（两个条件同时满足）：
1. 表格区域覆盖页面 ≥ `PROSE_TABLE_AREA_RATIO`——仅建块侧可得；
   L1 侧的 L0 表 bbox 即页框，天然满足，故由调用方决定是否需要该检查。
2. 内容像正文：任一 ≥120 字且带句末标点的单元格，
   或 ≥2 个 ≥20 字且带句末标点的单元格。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

SENTENCE_END: Final[str] = "。！？；：!?;:"
PROSE_CELL_MIN_CHARS: Final[int] = 120
PROSE_CELL_SHORT_MIN_CHARS: Final[int] = 20
PROSE_TABLE_AREA_RATIO: Final[float] = 0.6


def is_prose_cells(texts: Iterable[str]) -> bool:
    """单元格文字像正文而非行列数据。"""
    short_prose = 0
    for text in texts:
        stripped = text.strip()
        if len(stripped) < PROSE_CELL_SHORT_MIN_CHARS:
            continue
        if not any(mark in stripped for mark in SENTENCE_END):
            continue
        if len(stripped) >= PROSE_CELL_MIN_CHARS:
            return True
        short_prose += 1
    return short_prose >= 2
