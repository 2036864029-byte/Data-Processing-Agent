"""L0 锚点投影：用 Markdown 表的行/列结构匹配坐标文本项。

目的不是替代 L0 结构，而是：
- 给 L0 表一个真实 bbox，供与 L1 区域按空间重叠配对；
- 给 L0 单元格补坐标，便于渲染与回溯；
- 结构仍以 L0 为准，坐标只负责定位。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.table import bbox as bbox_math
from dataagent.pipeline.table.geometry import PositionedText


@dataclass(frozen=True, slots=True)
class ProjectedGrid:
    grid: TableGrid
    coverage: float


def project_grid(
    grid: TableGrid, items: Sequence[PositionedText], *, min_coverage: float = 0.2
) -> ProjectedGrid | None:
    if not items or not grid.cells:
        return None
    row_texts = [_row_text(grid, row) for row in range(grid.n_rows)]
    rows: list[list[PositionedText]] = [[] for _ in row_texts]
    for item in items:
        text = _normalize(item.text)
        if not text:
            continue
        row = _best_match(text, row_texts)
        if row is not None:
            rows[row].append(item)

    updated: list[TableCell] = []
    matched_cells = 0
    total_cells = 0
    matched_items: list[PositionedText] = []
    for cell in grid.cells:
        if not cell.text.strip():
            updated.append(cell)
            continue
        total_cells += 1
        cell_text = _normalize(cell.text)
        candidates = [
            item
            for item in rows[cell.row]
            if _match_score(_normalize(item.text), cell_text) >= _threshold(item.text)
        ]
        if not candidates:
            updated.append(cell)
            continue
        matched_cells += 1
        matched_items.extend(candidates)
        updated.append(
            cell.model_copy(update={"bbox": bbox_math.bbox_from_items(candidates)})
        )
    coverage = matched_cells / total_cells if total_cells else 0.0
    if coverage < min_coverage or not matched_items:
        return None
    return ProjectedGrid(
        grid=grid.model_copy(
            update={
                "cells": tuple(updated),
                "bbox": bbox_math.bbox_from_items(matched_items),
                "confidence": round(coverage, 4),
            }
        ),
        coverage=round(coverage, 4),
    )


def _row_text(grid: TableGrid, row: int) -> str:
    return "".join(
        _normalize(cell.text) for cell in grid.cells if cell.row == row
    )


def _best_match(text: str, anchors: Sequence[str]) -> int | None:
    best_row = None
    best_score = 0
    for row, anchor in enumerate(anchors):
        if not anchor:
            continue
        score = _match_score(text, anchor)
        if score > best_score:
            best_row, best_score = row, score
    if best_row is None or best_score < _threshold(text):
        return None
    return best_row


def _match_score(left: str, right: str) -> int:
    if not left or not right:
        return 0
    return SequenceMatcher(None, left, right, autojunk=False).find_longest_match(
        0, len(left), 0, len(right)
    ).size


def _threshold(text: str) -> int:
    normalized = _normalize(text)
    return max(1, min(4, len(normalized)))


def _normalize(text: str) -> str:
    return "".join(str(text).split())
