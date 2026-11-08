"""页面块构建：Markdown 回退路径与坐标项路径。

两条路径产出同一种 `Block` 序列（标题 / 段落 / 表格）：
- 无坐标项（非表格页、CSV/text、text_layer_recovered、纯扫描）走 Markdown 回退，
  bbox 取页框；
- 有坐标项走行聚类 + 段落合并，结构元素角色只作标题兜底信号。

标题层级本身不在这里定案——`pipeline.structure.headings` 在文档级归一化。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Final

from dataagent.contracts.headings import (
    MAX_HEADING_CHARS,
    HeadingSignal,
    classify_numbering,
    level_from_role,
)
from dataagent.pipeline.merge.merger import MergedPage
from dataagent.pipeline.quality.normalization import normalize_text
from dataagent.pipeline.quality.punctuation import SENTENCE_END
from dataagent.pipeline.structure.lines import (
    Line,
    cluster_lines,
    median_width,
    norm_text,
    starts_new_paragraph,
)
from dataagent.pipeline.structure.models import BBox, Block, TableSlot
from dataagent.pipeline.structure.slots import (
    inside_any,
    line_captions,
    markdown_captions,
)
from dataagent.pipeline.table import bbox as bbox_math
from dataagent.pipeline.table.geometry import PositionedText
from dataagent.pipeline.table.syntax import is_separator_cell

RUNNING_HEADER_MIN_PAGES: Final[int] = 3
HEADING_END_EXCLUDED: Final[str] = SENTENCE_END + "，、"

_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_ID_WIDTH: Final[int] = 3


def markdown_blocks(
    *,
    page_no: int,
    markdown: str,
    slots: Sequence[TableSlot],
    page_bbox: BBox,
    demoted_spans: Sequence[tuple[int, int]] = (),
    dropped_lines: frozenset[str] = frozenset(),
) -> list[Block]:
    lines = markdown.splitlines()
    slots, consumed = markdown_captions(lines, slots)
    by_start = {
        slot.span[0]: slot
        for slot in slots
        if slot.span is not None
    }
    demoted_by_start = dict(demoted_spans)
    trailing = [slot for slot in slots if slot.span is None]
    blocks: list[Block] = []
    paragraph: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        blocks.append(
            Block(
                kind="text",
                page_no=page_no,
                bbox=page_bbox,
                text="\n".join(paragraph),
            )
        )
        paragraph.clear()

    index = 0
    while index < len(lines):
        demoted_end = demoted_by_start.get(index)
        if demoted_end is not None:
            paragraph.extend(demoted_text(lines[index:demoted_end]))
            index = demoted_end
            continue
        slot = by_start.get(index)
        if slot is not None:
            flush()
            blocks.append(table_block(page_no, slot))
            index = slot.span[1] if slot.span is not None else index + 1
            continue
        if index in consumed:
            index += 1
            continue
        if norm_header(lines[index]) in dropped_lines:
            flush()
            index += 1
            continue
        match = _HEADING_RE.match(lines[index].strip())
        if match is not None:
            flush()
            blocks.append(
                Block(
                    kind="heading",
                    page_no=page_no,
                    bbox=page_bbox,
                    text=match.group(2).strip(),
                    level=len(match.group(1)),
                )
            )
            index += 1
            continue
        signal = promotable_numbering(lines[index])
        if signal is not None:
            flush()
            blocks.append(
                Block(
                    kind="heading",
                    page_no=page_no,
                    bbox=page_bbox,
                    text=lines[index].strip(),
                    level=signal.level,
                )
            )
            index += 1
            continue
        if not lines[index].strip():
            flush()
            index += 1
            continue
        paragraph.append(lines[index].rstrip())
        index += 1
    flush()
    blocks.extend(table_block(page_no, slot) for slot in trailing)
    return blocks


def item_blocks(
    *,
    page_no: int,
    markdown: str,
    items: Sequence[PositionedText],
    slots: Sequence[TableSlot],
    page_bbox: BBox,
    roles: Mapping[int, str],
    dropped_lines: frozenset[str] = frozenset(),
) -> tuple[list[Block], list[str]]:
    headings = heading_entries(markdown)
    remaining = [item for item in items if not inside_any(item, slots)]
    lines = cluster_lines(remaining)
    lines, consumed, slots = line_captions(lines, slots)
    median = median_width(lines)
    matched: set[int] = set()
    blocks: list[Block] = []
    paragraph: list[Line] = []
    warnings: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        blocks.append(
            Block(
                kind="text",
                page_no=page_no,
                bbox=bbox_math.union_bbox([line.bbox for line in paragraph]),
                text=join_lines([line.text for line in paragraph]),
            )
        )
        paragraph.clear()

    for line in lines:
        if line.norm in consumed:
            continue
        if norm_header(line.text) in dropped_lines:
            flush()
            continue
        heading_index = match_heading(line.norm, headings, matched)
        if heading_index is not None:
            flush()
            blocks.append(
                Block(
                    kind="heading",
                    page_no=page_no,
                    bbox=line.bbox,
                    text=headings[heading_index][1],
                    level=headings[heading_index][0],
                )
            )
            matched.add(heading_index)
            continue
        level = promoted_level(line, roles)
        if level is not None:
            flush()
            blocks.append(
                Block(
                    kind="heading",
                    page_no=page_no,
                    bbox=line.bbox,
                    text=line.text.strip(),
                    level=level,
                )
            )
            continue
        if paragraph and starts_new_paragraph(paragraph[-1], line, median):
            flush()
        paragraph.append(line)
    flush()
    blocks.extend(table_block(page_no, slot) for slot in slots)
    blocks.sort(key=lambda block: (-block.bbox[3], block.bbox[0]))

    unmatched = [
        (level, text)
        for index, (level, text, _norm) in enumerate(headings)
        if index not in matched
    ]
    for level, text in unmatched:
        blocks.append(
            Block(
                kind="heading",
                page_no=page_no,
                bbox=page_bbox,
                text=text,
                level=level,
            )
        )
    if unmatched:
        warnings.append(f"heading_unmatched:{page_no}")
    return blocks, warnings


def heading_entries(markdown: str) -> list[tuple[int, str, str]]:
    entries: list[tuple[int, str, str]] = []
    for line in markdown.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match is not None:
            text = match.group(2).strip()
            entries.append((len(match.group(1)), text, norm_text(text)))
    return entries


def match_heading(
    norm: str,
    headings: Sequence[tuple[int, str, str]],
    matched: set[int],
) -> int | None:
    for index, (_level, _text, heading_norm) in enumerate(headings):
        if index not in matched and heading_norm == norm:
            return index
    return None


def promotable_numbering(text: str) -> HeadingSignal | None:
    """结构性中文编号（章/节/一、/（一））本身即可成为标题。"""
    signal = classify_numbering(text)
    if signal is None or not signal.kind.promotable:
        return None
    return signal


def promoted_level(line: Line, roles: Mapping[int, str]) -> int | None:
    """坐标项行 → 标题层级：编号优先，tagged PDF 角色兜底。"""
    signal = promotable_numbering(line.text)
    if signal is not None:
        return signal.level
    return role_level(line, roles)


def role_level(line: Line, roles: Mapping[int, str]) -> int | None:
    """H1–H6 角色兜底：只收短行且非句末标点，避免把正文提升为标题。"""
    text = line.text.strip()
    if not text or len(text) > MAX_HEADING_CHARS or text[-1] in HEADING_END_EXCLUDED:
        return None
    for item in line.items:
        if item.mcid is None:
            continue
        level = level_from_role(roles.get(item.mcid, ""))
        if level is not None:
            return level
    return None


def join_lines(texts: Sequence[str]) -> str:
    result = ""
    for text in texts:
        piece = normalize_text(text).strip()
        if not piece:
            continue
        if result and needs_space(result[-1], piece[0]):
            result += " "
        result += piece
    return result


def needs_space(left: str, right: str) -> bool:
    return left.isascii() and left.isalnum() and right.isascii() and right.isalnum()


def table_block(page_no: int, slot: TableSlot) -> Block:
    return Block(kind="table", page_no=page_no, bbox=slot.bbox, table=slot)


def demoted_text(lines: Sequence[str]) -> list[str]:
    """把伪表格行还原成纯文本（去管道与分隔行）。"""
    texts: list[str] = []
    for line in lines:
        stripped = line.strip().strip("|")
        cells = [cell.strip() for cell in stripped.split("|")]
        if cells and all(is_separator_cell(cell) for cell in cells if cell):
            continue
        joined = " ".join(cell for cell in cells if cell)
        if joined:
            texts.append(joined)
    return texts


def running_headers(pages: Sequence[MergedPage]) -> frozenset[str]:
    """跨页重复的 <u>…</u> 行视为页眉，按页计数 ≥3 次才剔除。"""
    counter: Counter[str] = Counter()
    for page in pages:
        seen: set[str] = set()
        for line in page.markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("<u>") and stripped.endswith("</u>"):
                seen.add(norm_header(stripped))
        counter.update(seen)
    return frozenset(
        text for text, count in counter.items() if count >= RUNNING_HEADER_MIN_PAGES
    )


def norm_header(text: str) -> str:
    return norm_text(re.sub(r"</?u>", "", text))


def table_id(index: int) -> str:
    return f"t{index:0{_TABLE_ID_WIDTH}d}"
