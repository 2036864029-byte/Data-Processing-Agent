"""表格策略：验收、路由与伪表判定的阈值集中处。

这些数值原先散落在 `acceptance` / `router` / `prose` / `build` 四处，
改动一个判据需要全局搜索。现在只有这一份定义，并可经
`TaskConfig.table_policy` 注入；默认值等于迁移前的行为。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final


@unique
class TableSource(StrEnum):
    """表格来源（路由择优与观测都用它，别再写字面量）。"""

    MARKDOWN = "markdown"
    GEOMETRY = "geometry"
    RULED = "ruled"


@dataclass(frozen=True, slots=True)
class TablePolicy:
    # 验收网关：候选表能否替换 L0
    min_rows: int = 2
    min_cols: int = 2
    min_filled_ratio: float = 0.15
    min_text_coverage: float = 0.85
    min_row_ratio: float = 0.5
    # 路由：L0 / L1 / 有框线三方配对与择优
    selection_margin: float = 0.05
    pair_min_overlap: float = 0.15
    fragment_overlap: float = 0.5
    l0_suppress_coverage: float = 0.8
    # 无框线表：pdfplumber text 策略候选。
    # 2026-11-08 评估（19 份文档 × 前 10 页）：0 次优于 L0 Markdown，默认关闭。
    enable_text_strategy: bool = False
    # 伪表：页眉假表 / 整页正文被识别成表
    prose_cell_min_chars: int = 120
    prose_cell_short_min_chars: int = 20
    prose_short_cell_count: int = 2
    prose_table_area_ratio: float = 0.6


DEFAULT_TABLE_POLICY: Final[TablePolicy] = TablePolicy()
