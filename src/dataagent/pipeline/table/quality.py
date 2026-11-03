"""表格质量：用于 L0/L1 候选择优的确定性指标。"""

from __future__ import annotations

from dataclasses import dataclass

from dataagent.contracts.table import TableGrid


@dataclass(frozen=True, slots=True)
class GridQuality:
    cell_fill: float
    header_ok: bool
    confidence: float
    score: float


def grid_quality(grid: TableGrid) -> GridQuality:
    total = grid.n_rows * grid.n_cols
    filled_area = sum(
        cell.rowspan * cell.colspan
        for cell in grid.cells
        if cell.text.strip()
    )
    cell_fill = filled_area / total if total else 0.0
    header_ok = grid.header_rows > 0 and any(
        cell.text.strip() for cell in grid.cells if cell.row < grid.header_rows
    )
    score = (
        0.60 * cell_fill
        + 0.25 * (1.0 if header_ok else 0.0)
        + 0.15 * grid.confidence
    )
    return GridQuality(
        cell_fill=round(cell_fill, 4),
        header_ok=header_ok,
        confidence=grid.confidence,
        score=round(score, 4),
    )
