"""T1 后处理算子库（B6/T105）。

P0 重点自建 5 个纯 CPU / 毫秒级算子（无需重跑解析）：
- `rebuild_table_cells`：Markdown 表格行补齐到统一列宽（cell 坐标重建属后续增强）；
- `strip_header_footer`：剔除页码样式的首尾行；
- `merge_cross_page_table`：相邻页表头一致时合并表体；
- `reorder_blocks` / `resegment_columns`：缺少 `text_items` 坐标时诚实返回 unchanged。

需要换输入 / 换参数的算子（rotate / deskew / extract_region / ocr_page）在 P0
返回 unchanged 并在 notes 说明所需路径——不伪造修复。

算子禁止 IO（G9 grep 门禁：pipeline/repair 内不得出现 open / requests / db）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.defects import Defect, RepairOperator
from dataagent.pipeline.repair.operators.base import (
    OperatorContext,
    OperatorFn,
    OperatorOutcome,
)

_SEPARATOR_CELL_RE: Final[re.Pattern[str]] = re.compile(r":?-{3,}:?")
_PAGE_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:第\s*\d+\s*页|page\s+\d+(?:\s*(?:/|of)\s*\d+)?|\d+\s*/\s*\d+|[-–—]\s*\d+\s*[-–—])\s*$",
    re.IGNORECASE,
)


def rebuild_table_cells(
    artifact: PageArtifact, defect: Defect, context: OperatorContext
) -> OperatorOutcome:
    lines = artifact.markdown.splitlines()
    blocks = _table_blocks(lines)
    if not blocks:
        return _unchanged("未发现 Markdown 表格")

    new_lines = list(lines)
    changed = False
    for start, end in blocks:
        normalized = _normalize_block(lines[start:end])
        changed = changed or normalized != lines[start:end]
        new_lines[start:end] = normalized
    if not changed:
        return _unchanged("表格列宽已一致")
    return _candidate(artifact, new_lines, "Markdown 表格行列已补齐")


def strip_header_footer(
    artifact: PageArtifact, defect: Defect, context: OperatorContext
) -> OperatorOutcome:
    lines = artifact.markdown.splitlines()
    start, end = 0, len(lines)
    while start < end and _strippable(lines[start]):
        start += 1
    while end > start and _strippable(lines[end - 1]):
        end -= 1
    if start == 0 and end == len(lines):
        return _unchanged("未发现页眉页脚/页码行")

    new_lines = lines[start:end]
    if not new_lines:
        return _candidate(artifact, "", "页眉页脚剔除后为空页")
    return _candidate(artifact, new_lines, "已剔除页眉页脚/页码行")


def merge_cross_page_table(
    artifact: PageArtifact, defect: Defect, context: OperatorContext
) -> OperatorOutcome:
    if context.next_artifact is None:
        return _unchanged("缺少相邻页产物，跨页表合并需下一页上下文")

    current_lines = artifact.markdown.splitlines()
    next_lines = context.next_artifact.markdown.splitlines()
    current_blocks = _table_blocks(current_lines)
    next_blocks = _table_blocks(next_lines)
    if not current_blocks or not next_blocks:
        return _unchanged("相邻页缺少表格")

    current_start, current_end = current_blocks[-1]
    next_start, next_end = next_blocks[0]
    if _header_cells(current_lines[current_start]) != _header_cells(
        next_lines[next_start]
    ):
        return _unchanged("相邻页表头不一致，不做跨页合并")

    body_start = next_start + 1
    if body_start < next_end and _is_separator(
        _split_cells(next_lines[body_start])
    ):
        body_start += 1
    body = next_lines[body_start:next_end]
    if not body:
        return _unchanged("相邻页表格无表体")

    new_lines = (
        current_lines[:current_end] + body + current_lines[current_end:]
    )
    return _candidate(artifact, new_lines, "跨页表体已合并到当前页")


def _unchanged(note: str) -> OperatorOutcome:
    return OperatorOutcome(candidate=None, changed=False, cost=0, notes=note)


def _needs_rerun(note: str) -> OperatorFn:
    def operator(
        artifact: PageArtifact, defect: Defect, context: OperatorContext
    ) -> OperatorOutcome:
        return OperatorOutcome(candidate=None, changed=False, cost=0, notes=note)

    return operator


def _candidate(
    artifact: PageArtifact, new_lines: list[str] | str, note: str
) -> OperatorOutcome:
    if isinstance(new_lines, list):
        markdown = "\n".join(new_lines)
        if artifact.markdown.endswith("\n"):
            markdown += "\n"
    else:
        markdown = new_lines
    if markdown == artifact.markdown:
        return _unchanged("内容无变化")
    return OperatorOutcome(
        candidate=artifact.model_copy(update={"markdown": markdown}),
        changed=True,
        cost=0,
        notes=note,
    )


def _table_blocks(lines: list[str]) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        if not _is_table_line(lines[index]):
            index += 1
            continue
        start = index
        while index < len(lines) and _is_table_line(lines[index]):
            index += 1
        blocks.append((start, index))
    return blocks


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def _split_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _join_cells(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _is_separator(cells: list[str]) -> bool:
    non_empty = [cell for cell in cells if cell]
    return bool(non_empty) and all(
        _SEPARATOR_CELL_RE.fullmatch(cell) for cell in non_empty
    )


def _normalize_block(block: list[str]) -> list[str]:
    rows = [_split_cells(line) for line in block]
    width = max(len(row) for row in rows)
    normalized: list[str] = []
    for line, row in zip(block, rows, strict=True):
        if _is_separator(row):
            normalized.append(line)
        else:
            normalized.append(_join_cells([*row, *([""] * (width - len(row)))]))
    return normalized


def _strippable(line: str) -> bool:
    return not line.strip() or _PAGE_NUMBER_RE.match(line) is not None


def _header_cells(line: str) -> list[str]:
    return [cell.lower() for cell in _split_cells(line)]


OPERATORS: Final[Mapping[RepairOperator, OperatorFn]] = {
    RepairOperator.REBUILD_TABLE_CELLS: rebuild_table_cells,
    RepairOperator.STRIP_HEADER_FOOTER: strip_header_footer,
    RepairOperator.MERGE_CROSS_PAGE_TABLE: merge_cross_page_table,
    RepairOperator.REORDER_BLOCKS: _needs_rerun(
        "缺少 text_items 坐标；块重排需 T2 重跑后执行",
    ),
    RepairOperator.RESEGMENT_COLUMNS: _needs_rerun(
        "分栏重切需换输入（T2）后重跑解析",
    ),
    RepairOperator.ROTATE_PAGE: _needs_rerun(
        "页面旋转需更换输入（T2）后重跑解析",
    ),
    RepairOperator.DESKEW: _needs_rerun(
        "去斜需更换输入（T2）后重跑解析",
    ),
    RepairOperator.EXTRACT_REGION: _needs_rerun(
        "定点补抽需带区域参数重跑解析（T2）",
    ),
    RepairOperator.OCR_PAGE: _needs_rerun(
        "OCR 增强器未接入（P1）；P0 落 mark_only 降级",
    ),
}
