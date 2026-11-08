"""文本归一的唯一入口（Phase 3）。

三段流水固定为：抽取文本 → **归一化** → 建块/渲染。
本模块是「归一化」这一段的公共策略面，消费方只从这里取入口，
不得再自行组合 `normalize_cjk_text` 与 `repair_numbering`。

入口与适用范围：

| 入口 | 适用 | 行为 |
| --- | --- | --- |
| `normalize_text` | 正文 / Markdown | 伪空格 + 硬换行回流 + 编号修复 |
| `normalize_cell` | 单个表格单元格 | 伪空格 + 语义换行保留，不跨单元格 |
| `normalize_table_grid` | 整张网格 | 只改单元格文本，结构字段不动 |

机制（正则与回流规则）在 `text_normalize` / `numbering` 中，本模块只负责
组合出正确的口径：正文入口含编号修复，因此 Markdown 路径与坐标路径一致；
表格路径走单元格入口，不套用正文的硬换行规则。

三个入口均幂等：`normalize(normalize(x)) == normalize(x)`。正文入口为此收敛到
不动点——编号修复与去伪空格会互相制造新问题（`0()\\t中` → `（0）\\t中` →
`（0）中`），单一顺序无法一次到位。
"""

from __future__ import annotations

from typing import Final

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.quality.numbering import repair_numbering
from dataagent.pipeline.quality.text_normalize import (
    normalize_cell_text,
    normalize_cjk_text,
)
from dataagent.pipeline.quality.text_normalize import (
    normalize_table_grid as _normalize_table_grid,
)

# 每轮只做「修复编号 → 去伪空格/回流」，两者都是单调收敛（不会重新引入空格或
# 非规范括号），因此 4 轮足以覆盖相互触发；留上限避免病态输入死循环。
_MAX_NORMALIZE_PASSES: Final[int] = 4


def normalize_text(text: str) -> str:
    """正文 / Markdown 入口：编号修复 + 伪空格 + 硬换行回流（收敛到不动点）。"""
    current = text
    for _ in range(_MAX_NORMALIZE_PASSES):
        updated = normalize_cjk_text(repair_numbering(current))
        if updated == current:
            return current
        current = updated
    return current


def normalize_cell(text: str) -> str:
    """单个表格单元格入口：同一套 CJK 规则，但保留语义换行。"""
    return normalize_cell_text(text)


def normalize_table_grid(grid: TableGrid) -> TableGrid:
    """整张网格入口：逐单元格归一，行列/span/bbox 原样保留。"""
    return _normalize_table_grid(grid)
