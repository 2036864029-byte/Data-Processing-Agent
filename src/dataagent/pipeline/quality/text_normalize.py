"""CJK 文本归一：伪空格去除 + 硬换行回流。

只做确定性文本整形，不生成/猜测内容：
- 汉字之间由抽取器插入的空格删除（中文与拉丁文之间的空格保留）；
- 不含终止标点的段落行合并，遇终止标点 / 编号 / 列表 / 标题 / 表格边界停止；
- 表格块原样保留；幂等，可重复执行。

单元格另走 `normalize_cell_text` / `normalize_table_grid`：同一套 CJK 规则，
但行只在语义边界合并，且绝不跨单元格。
"""

from __future__ import annotations

import re
from typing import Final

from dataagent.contracts.headings import classify_numbering
from dataagent.contracts.table import TableGrid
from dataagent.pipeline.quality.punctuation import BODY_TERMINAL

_TERMINAL: Final[str] = BODY_TERMINAL
_CJK = (
    r"㐀-䶿一-鿿"
    r"，。！？；：、（）「」『』【】《》〈〉…—"
)
_PHANTOM_SPACE: Final[re.Pattern[str]] = re.compile(
    rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])"
)
_CELL_PHANTOM_SPACE: Final[re.Pattern[str]] = re.compile(
    rf"(?<=[{_CJK}])[ \t　]+(?=[{_CJK}])"
)
_CJK_CHAR: Final[re.Pattern[str]] = re.compile(rf"[{_CJK}]")
_NUMBERING: Final[re.Pattern[str]] = re.compile(
    r"^(第[一二三四五六七八九十百千0-9]+[条章节款]"
    r"|[一二三四五六七八九十]+、"
    r"|[（(]\d+[）)]"
    r"|[①-⑳]"
    r"|\d+[.、])"
)
_STRUCTURAL: Final[re.Pattern[str]] = re.compile(
    r"^(#{1,6}\s|[-*+]\s|>\s|\d+[.)]\s|```)"
)


def normalize_cjk_text(markdown: str) -> str:
    """返回归一后的 Markdown；输入为空或无需变化时原样返回。"""
    if not markdown.strip():
        return markdown
    normalized_newlines = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized_newlines.splitlines()
    output: list[str] = []
    current: str | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            output.append(current)
            current = None

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush()
            output.append("")
            index += 1
            continue

        if stripped.startswith("|"):
            flush()
            while index < len(lines) and lines[index].strip().startswith("|"):
                output.append(lines[index])
                index += 1
            continue

        cleaned = _strip_phantom_spaces(line)
        if _is_numbering(cleaned):
            flush()
            current = cleaned
            index += 1
            continue
        if _STRUCTURAL.match(cleaned.strip()):
            flush()
            output.append(cleaned)
            index += 1
            continue

        if current is not None and _can_join(current, cleaned):
            current = _join(current, cleaned)
        else:
            flush()
            current = cleaned
        index += 1

    flush()
    text = "\n".join(output)
    return text + "\n" if normalized_newlines.endswith("\n") else text


def normalize_cell_text(text: str) -> str:
    """表格单元格文本归一：去 CJK 伪空格 + 行尾硬换行按语义合并。

    与正文规则的区别：
    - 单元格内换行只在语义边界保留（句末标点、编号/列表起始行之前）；
    - 纯数字/拉丁多行不合并（避免把 `2024\\n2025` 并成一行）；
    - 跨单元格绝不合并——按单元格独立调用。

    幂等：`normalize_cell_text(normalize_cell_text(x)) == normalize_cell_text(x)`。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = [
        _CELL_PHANTOM_SPACE.sub("", line.strip())
        for line in normalized.split("\n")
        if line.strip()
    ]
    if not cleaned:
        return ""
    merged: list[str] = [cleaned[0]]
    for line in cleaned[1:]:
        previous = merged[-1]
        if _cell_joins(previous, line):
            merged[-1] = _join(previous, line)
        else:
            merged.append(line)
    return "\n".join(merged)


def normalize_table_grid(grid: TableGrid) -> TableGrid:
    """单元格文本归一；行列、rowspan/colspan、bbox 等结构字段原样保留。"""
    cells = tuple(
        cell.model_copy(update={"text": normalize_cell_text(cell.text)})
        for cell in grid.cells
    )
    if cells == grid.cells:
        return grid
    return grid.model_copy(update={"cells": cells})


def _cell_joins(previous: str, line: str) -> bool:
    """可合并且至少一侧含 CJK；编号起始行保持独立。"""
    if classify_numbering(line) is not None:
        return False
    if not (_CJK_CHAR.search(previous) or _CJK_CHAR.search(line)):
        return False
    return _can_join(previous, line)


def _strip_phantom_spaces(line: str) -> str:
    """删汉字间伪空格，但保留编号标记后的结构空格（如“第二十八条 采购人”）。"""
    match = _NUMBERING.match(line.lstrip())
    protected = line
    if match is not None:
        prefix_end = len(line) - len(line.lstrip()) + match.end()
        if prefix_end < len(line) and line[prefix_end] == " ":
            protected = line[:prefix_end] + "\x00" + line[prefix_end + 1 :]
    cleaned = _PHANTOM_SPACE.sub("", protected)
    return cleaned.replace("\x00", " ")


def _is_numbering(line: str) -> bool:
    return bool(_NUMBERING.match(line.strip()))


def _is_structural(line: str) -> bool:
    stripped = line.strip()
    return bool(_NUMBERING.match(stripped) or _STRUCTURAL.match(stripped))


def _can_join(previous: str, line: str) -> bool:
    text = previous.rstrip()
    if not text:
        return False
    if text[-1] in _TERMINAL:
        return False
    return not _is_structural(line)


def _join(previous: str, line: str) -> str:
    left = previous.rstrip()
    right = line.lstrip()
    if not left:
        return right
    if not right:
        return left
    separator = "" if _is_cjk(left[-1]) and _is_cjk(right[0]) else " "
    return f"{left}{separator}{right}"


def _is_cjk(char: str) -> bool:
    return bool(re.match(rf"[{_CJK}]", char))
