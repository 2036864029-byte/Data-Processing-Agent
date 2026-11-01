"""T1 版面修复（B6 扩展）：伪表格解包 + 中文编号层级重建。

针对 pdf-inspector 在网页打印件上的两类典型误判：
1. 伪表格：页眉/页脚对齐触发表格聚类，整页正文被塞进一个单元格；
   判据：表格只有 ≤1 个正文行，且单个单元格字数占本页文本 >60%。
2. 中文编号层级：`第X章` / `第X条` / `一、` / `（一）` 是纯文本，
   引擎不会生成标题或分段；本模块按编号规则插入段落边界与标题标记。

只做确定性文本变换，无 IO；不改动表格内其余行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

PSEUDO_TABLE_CELL_RATIO: Final[float] = 0.6
PSEUDO_TABLE_MIN_CHARS: Final[int] = 200

_CHAPTER_RE: Final[re.Pattern[str]] = re.compile(
    r"^第[一二三四五六七八九十百零〇\d]+章"
)
_TOP_SECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"^[一二三四五六七八九十]+、"
)
_ARTICLE_RE: Final[re.Pattern[str]] = re.compile(
    r"^第[一二三四五六七八九十百零〇\d]+条"
)
_ITEM_RE: Final[re.Pattern[str]] = re.compile(
    r"^（[一二三四五六七八九十]+）"
)
_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"第[一二三四五六七八九十百零〇\d]+[条章]"
    r"|（[一二三四五六七八九十]+）"
    r"|(?:^|(?<=\s))[一二三四五六七八九十]+、",
    re.MULTILINE,
)
_SEPARATOR_CELL_RE: Final[re.Pattern[str]] = re.compile(r":?-{3,}:?")


@dataclass(frozen=True, slots=True)
class TableStats:
    rows: int
    columns: int
    cells: int
    max_cell_chars: int


@dataclass(frozen=True, slots=True)
class NormalizationOutcome:
    markdown: str
    unpacked_tables: int
    headings_added: int


def normalize_layout(markdown: str) -> NormalizationOutcome:
    """伪表格解包 → 中文编号层级重建；无变化时原样返回。"""
    unpacked, table_count = _unpack_pseudo_tables(markdown)
    rebuilt, headings = _segment_hierarchy(unpacked)
    return NormalizationOutcome(
        markdown=rebuilt, unpacked_tables=table_count, headings_added=headings
    )


def table_stats(markdown: str) -> list[TableStats]:
    return [_stats(block) for block in _table_blocks(markdown)]


def is_plausible_table(stats: TableStats, *, page_chars: int) -> bool:
    """单行巨单元格 = 伪表格；单列表格不视为表格。"""
    if stats.columns < 2 or stats.rows < 1:
        return False
    body_rows = max(0, stats.rows - 1)
    return not (
        body_rows <= 1
        and stats.max_cell_chars
        >= max(PSEUDO_TABLE_MIN_CHARS, page_chars * PSEUDO_TABLE_CELL_RATIO)
    )


def _unpack_pseudo_tables(markdown: str) -> tuple[str, int]:
    page_chars = len(_content_chars(markdown))
    parts: list[str] = []
    unpacked = 0
    index = 0
    for start, end, should_unpack in _table_block_flags(markdown, page_chars):
        parts.append(markdown[index:start])
        if should_unpack:
            content = _largest_cell(markdown[start:end].splitlines())
            parts.append(content + "\n")
            unpacked += 1
        else:
            parts.append(markdown[start:end])
        index = end
    parts.append(markdown[index:])
    return "".join(parts), unpacked


def _table_block_flags(
    markdown: str, page_chars: int
) -> list[tuple[int, int, bool]]:
    flags: list[tuple[int, int, bool]] = []
    lines = markdown.splitlines(keepends=True)
    offset = 0
    index = 0
    while index < len(lines):
        if not lines[index].strip().startswith("|"):
            offset += len(lines[index])
            index += 1
            continue
        start = offset
        block: list[str] = []
        while index < len(lines) and lines[index].strip().startswith("|"):
            block.append(lines[index])
            offset += len(lines[index])
            index += 1
        stats = _stats(block)
        flags.append(
            (start, offset, not is_plausible_table(stats, page_chars=page_chars))
        )
    return flags


def _stats(block: list[str]) -> TableStats:
    rows = [
        _split_cells(line)
        for line in block
        if line.strip().startswith("|") and not _is_separator(_split_cells(line))
    ]
    return TableStats(
        rows=len(rows),
        columns=max((len(row) for row in rows), default=0),
        cells=sum(len(row) for row in rows),
        max_cell_chars=max(
            (len(cell) for row in rows for cell in row), default=0
        ),
    )


def _largest_cell(lines: list[str]) -> str:
    cells = [
        cell
        for line in lines
        if not _is_separator(_split_cells(line))
        for cell in _split_cells(line)
    ]
    return max(cells, key=len, default="").strip()


def _segment_hierarchy(markdown: str) -> tuple[str, int]:
    output: list[str] = []
    headings = 0
    for is_table, lines in _split_table_blocks(markdown):
        if is_table:
            output.extend(lines)
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if output and output[-1] != "":
                    output.append("")
                continue
            if stripped.startswith("#"):
                output.append(stripped)
                continue
            for segment in _split_markers(stripped):
                if _CHAPTER_RE.match(segment):
                    _ensure_blank(output)
                    output.append("# " + segment)
                    headings += 1
                elif _TOP_SECTION_RE.match(segment):
                    _ensure_blank(output)
                    output.append("## " + segment)
                    headings += 1
                elif _ARTICLE_RE.match(segment) or _ITEM_RE.match(segment):
                    _ensure_blank(output)
                    output.append(segment)
                else:
                    output.append(segment)
    while output and output[-1] == "":
        output.pop()
    return ("\n".join(output) + "\n" if output else ""), headings


def _ensure_blank(output: list[str]) -> None:
    if output and output[-1] != "":
        output.append("")


def _split_markers(text: str) -> list[str]:
    positions = [match.start() for match in _MARKER_RE.finditer(text)]
    starts = [position for position in positions if position > 0]
    segments: list[str] = []
    previous = 0
    for position in starts:
        segment = text[previous:position].strip()
        if segment:
            segments.append(segment)
        previous = position
    tail = text[previous:].strip()
    if tail:
        segments.append(tail)
    return segments


def _split_table_blocks(markdown: str) -> list[tuple[bool, list[str]]]:
    blocks: list[tuple[bool, list[str]]] = []
    current: list[str] = []
    in_table = False
    for line in markdown.splitlines():
        is_table = line.strip().startswith("|")
        if is_table != in_table and current:
            blocks.append((in_table, current))
            current = []
        in_table = is_table
        current.append(line)
    if current:
        blocks.append((in_table, current))
    return blocks


def _table_blocks(markdown: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in markdown.splitlines():
        if line.strip().startswith("|"):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _split_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator(cells: list[str]) -> bool:
    non_empty = [cell for cell in cells if cell]
    return bool(non_empty) and all(
        _SEPARATOR_CELL_RE.fullmatch(cell) for cell in non_empty
    )


def _content_chars(markdown: str) -> str:
    """页面文本总量：非表格行 + 表格单元格文本（用于伪表格占比判断）。"""
    parts: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("|"):
            cells = _split_cells(stripped)
            if _is_separator(cells):
                continue
            parts.extend(cell for cell in cells if cell)
        else:
            parts.append(stripped)
    return "".join(parts)
