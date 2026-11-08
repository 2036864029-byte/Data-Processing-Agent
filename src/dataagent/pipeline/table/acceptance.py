"""表格候选验收网关：有框线抽取结果是否允许替换 L0。

网关只回答“是否允许替换”，不做择优打分：
- 结构下限：至少 2 行 2 列、非空占比达标；
- 内容下限：L0 文本字符覆盖率达标（防止丢内容）；
- 行覆盖下限：行数不得显著少于 L0（防止只抓到半张表）；
- 多值单元格：同一格出现多个日期/金额，视为合并单元格未拆开；
- bbox 必须落在页面范围内。

任一检查失败即回退 L0，并返回明确原因，供观测与回归统计。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.table.policy import DEFAULT_TABLE_POLICY, TablePolicy

BBOX_TOLERANCE_RATIO = 0.05
CELL_REGION_TOLERANCE = 1.0

_DATE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
_AMOUNT = re.compile(r"(?<![\d.])-?\d[\d,]*\.\d{2}(?![\d])")


@dataclass(frozen=True, slots=True)
class GateStats:
    n_rows: int
    n_cols: int
    filled_ratio: float
    text_coverage: float | None
    row_ratio: float | None
    multi_value_cells: int


@dataclass(frozen=True, slots=True)
class GateDecision:
    accepted: bool
    reason: str
    stats: GateStats


def evaluate_candidate(
    candidate: TableGrid,
    reference: TableGrid | None,
    *,
    page_bbox: tuple[float, float, float, float] | None = None,
    policy: TablePolicy = DEFAULT_TABLE_POLICY,
) -> GateDecision:
    stats = GateStats(
        n_rows=candidate.n_rows,
        n_cols=candidate.n_cols,
        filled_ratio=round(_filled_ratio(candidate), 4),
        text_coverage=(
            round(coverage_of(reference, [candidate], region=candidate.bbox), 4)
            if reference is not None
            else None
        ),
        row_ratio=(
            round(candidate.n_rows / reference.n_rows, 4)
            if reference is not None
            else None
        ),
        multi_value_cells=_multi_value_cells(candidate),
    )
    reason = _first_failure(candidate, stats, page_bbox, policy)
    return GateDecision(
        accepted=reason is None,
        reason=reason or "accepted",
        stats=stats,
    )


def coverage_of(
    target: TableGrid,
    sources: Sequence[TableGrid],
    *,
    region: tuple[float, float, float, float] | None = None,
) -> float:
    """target 的字符被 sources 覆盖的比例（忽略空白）。

    region 给定时，只统计与 region 相交（或没有 bbox）的 target 单元格，
    用于排除 L0 把标题/页脚误并进表格时的干扰。
    """
    target_counts = _char_counts(target, region=region)
    total = sum(target_counts.values())
    if total == 0:
        return 1.0
    source_counts: Counter[str] = Counter()
    for source in sources:
        source_counts.update(_char_counts(source))
    covered = sum(
        min(count, source_counts.get(char, 0))
        for char, count in target_counts.items()
    )
    return covered / total


def _first_failure(
    candidate: TableGrid,
    stats: GateStats,
    page_bbox: tuple[float, float, float, float] | None,
    policy: TablePolicy,
) -> str | None:
    if stats.n_rows < policy.min_rows or stats.n_cols < policy.min_cols:
        return "too_small"
    if stats.filled_ratio < policy.min_filled_ratio:
        return "low_fill"
    if (
        stats.text_coverage is not None
        and stats.text_coverage < policy.min_text_coverage
    ):
        return "text_loss"
    if stats.row_ratio is not None and stats.row_ratio < policy.min_row_ratio:
        return "row_loss"
    if stats.multi_value_cells > 0:
        return "multi_value_cell"
    if page_bbox is not None and not _inside_page(candidate.bbox, page_bbox):
        return "bbox_outside"
    return None


def _filled_ratio(grid: TableGrid) -> float:
    total = grid.n_rows * grid.n_cols
    if total <= 0:
        return 0.0
    filled = sum(
        cell.rowspan * cell.colspan for cell in grid.cells if cell.text.strip()
    )
    return filled / total


def _char_counts(
    grid: TableGrid,
    *,
    region: tuple[float, float, float, float] | None = None,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for cell in grid.cells:
        if region is not None and not _in_region(cell.bbox, region):
            continue
        counts.update(_normalize(cell.text))
    return counts


def _in_region(
    bbox: tuple[float, float, float, float] | None,
    region: tuple[float, float, float, float],
) -> bool:
    if bbox is None:
        return True
    x0, y0, x1, y1 = bbox
    rx0, ry0, rx1, ry1 = region
    return (
        x1 >= rx0 - CELL_REGION_TOLERANCE
        and x0 <= rx1 + CELL_REGION_TOLERANCE
        and y1 >= ry0 - CELL_REGION_TOLERANCE
        and y0 <= ry1 + CELL_REGION_TOLERANCE
    )


def _normalize(text: str) -> str:
    return "".join(text.split())


def _multi_value_cells(grid: TableGrid) -> int:
    return sum(1 for cell in grid.cells if _is_multi_value(cell.text))


def _is_multi_value(text: str) -> bool:
    return len(_DATE.findall(text)) >= 2 or len(_AMOUNT.findall(text)) >= 2


def _inside_page(
    bbox: tuple[float, float, float, float],
    page_bbox: tuple[float, float, float, float],
) -> bool:
    page_x0, page_y0, page_x1, page_y1 = page_bbox
    tolerance_x = (page_x1 - page_x0) * BBOX_TOLERANCE_RATIO
    tolerance_y = (page_y1 - page_y0) * BBOX_TOLERANCE_RATIO
    x0, y0, x1, y1 = bbox
    return (
        x0 >= page_x0 - tolerance_x
        and x1 <= page_x1 + tolerance_x
        and y0 >= page_y0 - tolerance_y
        and y1 <= page_y1 + tolerance_y
    )
