"""PR-1 建块：统一行聚类 + 表格槽位 + bbox 吞并安全阀。

决策依据（2026-11-05 原型）：
- 结构元素可作为标题角色来源，但 P 粒度是逐文本 run（茅台 p25：147 项 / 381 个 P），
  不能当段落边界；因此 tagged/untagged 统一用「坐标项行聚类 + 段落合并」。
- 标题层级以 Markdown 的 `#` 个数为准（PR-1 不改变现有标题口径）。

无坐标项页面（非表格页、CSV/text、text_layer_recovered、纯扫描）走 Markdown 块回退，
bbox 取页框；表格仍按行范围替换为 TableGrid。
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.merge.merger import MergedDocument, MergedPage
from dataagent.pipeline.quality.text_normalize import normalize_cjk_text
from dataagent.pipeline.structure.models import (
    BBox,
    Block,
    DocumentBlocks,
    PageBlocks,
    PageBuildResult,
    PageLayout,
    TableSlot,
)
from dataagent.pipeline.table.geometry import PositionedText
from dataagent.pipeline.table.markdown import markdown_table_spans, markdown_tables
from dataagent.pipeline.table.router import TableRouting, route_tables

LINE_TOLERANCE_RATIO: Final[float] = 0.6
PARAGRAPH_SHORT_RATIO: Final[float] = 0.85
PARAGRAPH_GAP_RATIO: Final[float] = 1.6
BBOX_TRUST_AREA_RATIO: Final[float] = 0.6
SLOT_OVERLAP_RATIO: Final[float] = 0.15
CAPTION_MAX_CHARS: Final[int] = 60
CAPTION_SEARCH_DEPTH: Final[int] = 3
PROSE_CELL_MIN_CHARS: Final[int] = 120
PROSE_ROW_MIN_CHARS: Final[int] = 240
RUNNING_HEADER_MIN_PAGES: Final[int] = 3
SENTENCE_END: Final[str] = "。！？；：!?;:"
_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^(#{1,6})\s+(.*)$")
_CAPTION_RE: Final[re.Pattern[str]] = re.compile(r"表\s*\d+[-—.]?\d*")
_SEPARATOR_CELL_RE: Final[re.Pattern[str]] = re.compile(r"^:?-{3,}:?$")
_TABLE_ID_WIDTH: Final[int] = 3


@dataclass(slots=True)
class _Line:
    center: float
    height: float
    items: list[PositionedText]
    x0: float
    x1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def text(self) -> str:
        return "".join(item.text for item in sorted(self.items, key=lambda i: i.x))

    @property
    def norm(self) -> str:
        return _norm(self.text)

    @property
    def bbox(self) -> BBox:
        return (
            min(item.x for item in self.items),
            min(item.y for item in self.items),
            max(item.x + item.width for item in self.items),
            max(item.y + item.height for item in self.items),
        )


def build_document_blocks(
    document: MergedDocument,
    *,
    layouts: Mapping[int, PageLayout],
    ruled_tables: Callable[[int], Sequence[TableGrid]] | None = None,
) -> DocumentBlocks:
    pages: list[PageBlocks] = []
    warnings: list[str] = []
    table_index = 0
    dropped_lines = _running_headers(document.pages)
    for page in document.pages:
        if page.gap:
            pages.append(PageBlocks(page_no=page.page_no))
            continue
        layout = layouts.get(page.page_no, PageLayout(page_no=page.page_no))
        ruled = tuple(ruled_tables(page.page_no)) if ruled_tables is not None else ()
        result = build_page_blocks(
            page_no=page.page_no,
            markdown=page.markdown,
            layout=layout,
            ruled_tables=ruled,
            dropped_lines=dropped_lines,
        )
        assigned: list[Block] = []
        for block in result.page.blocks:
            if block.kind == "table" and block.table is not None:
                table_index += 1
                slot = replace(block.table, table_id=_table_id(table_index))
                block = replace(block, table=slot)
            assigned.append(block)
        pages.append(replace(result.page, blocks=tuple(assigned)))
        warnings.extend(result.warnings)
    return DocumentBlocks(
        doc_id=document.doc_id, pages=tuple(pages), warnings=tuple(warnings)
    )


def build_page_blocks(
    *,
    page_no: int,
    markdown: str,
    layout: PageLayout,
    ruled_tables: Sequence[TableGrid] = (),
    dropped_lines: frozenset[str] = frozenset(),
) -> PageBuildResult:
    page_bbox = _page_bbox(layout.items)
    l0_grids = markdown_tables(markdown, page_no=page_no, bbox=page_bbox)
    spans = markdown_table_spans(markdown)
    routing = route_tables(
        markdown, layout.items, page_no=page_no, ruled_tables=ruled_tables
    )
    slots, demoted, warnings = _build_slots(
        routing,
        l0_grids,
        spans,
        page_bbox,
        page_no=page_no,
        has_items=bool(layout.items),
    )
    if layout.items and all(slot.trusted_bbox for slot in slots):
        blocks, item_warnings = _item_blocks(
            page_no=page_no,
            markdown=markdown,
            items=layout.items,
            slots=slots,
            page_bbox=page_bbox,
            dropped_lines=dropped_lines,
        )
        warnings.extend(item_warnings)
    else:
        blocks = _markdown_blocks(
            page_no=page_no,
            markdown=markdown,
            slots=slots,
            page_bbox=page_bbox,
            demoted_spans=demoted,
            dropped_lines=dropped_lines,
        )
    return PageBuildResult(
        page=PageBlocks(page_no=page_no, blocks=tuple(blocks)),
        warnings=tuple(warnings),
    )


# ── 表格槽位 ────────────────────────────────────────────────────────────


def _build_slots(
    routing: TableRouting,
    l0_grids: Sequence[TableGrid],
    spans: Sequence[tuple[int, int]],
    page_bbox: BBox,
    *,
    page_no: int,
    has_items: bool,
) -> tuple[list[TableSlot], list[tuple[int, int]], list[str]]:
    slots: list[TableSlot] = []
    used_l0: set[int] = set()
    for choice in routing.tables:
        index = _match_l0(choice.grid, l0_grids, used_l0)
        span = None
        if index is not None:
            used_l0.add(index)
            span = spans[index] if index < len(spans) else None
        slots.append(
            TableSlot(
                grid=choice.grid,
                bbox=choice.grid.bbox,
                source=choice.source,
                trusted_bbox=_trusted_bbox(choice.grid.bbox, page_bbox),
                span=span,
            )
        )
    for index, grid in enumerate(l0_grids):
        if index in used_l0:
            continue
        slots.append(
            TableSlot(
                grid=grid,
                bbox=grid.bbox,
                source="markdown",
                trusted_bbox=_trusted_bbox(grid.bbox, page_bbox),
                span=spans[index] if index < len(spans) else None,
            )
        )
    demoted: list[tuple[int, int]] = []
    if not has_items:
        kept: list[TableSlot] = []
        for slot in slots:
            if slot.span is not None and _is_prose_table(slot):
                demoted.append(slot.span)
            else:
                kept.append(slot)
        slots = kept
    warnings: list[str] = []
    if has_items and any(not slot.trusted_bbox for slot in slots):
        warnings.append(f"table_bbox_untrusted:{page_no}")
    if demoted:
        warnings.append(f"table_l0_prose_demoted:{page_no}")
    return slots, demoted, warnings


def _match_l0(
    grid: TableGrid, l0_grids: Sequence[TableGrid], used: set[int]
) -> int | None:
    signature = _signature(grid)
    for index, l0 in enumerate(l0_grids):
        if index not in used and _signature(l0) == signature:
            return index
    best: tuple[float, int] | None = None
    for index, l0 in enumerate(l0_grids):
        if index in used:
            continue
        ratio = _overlap_ratio(grid.bbox, l0.bbox)
        if ratio >= SLOT_OVERLAP_RATIO and (best is None or ratio > best[0]):
            best = (ratio, index)
    return None if best is None else best[1]


def _signature(grid: TableGrid) -> tuple[tuple[int, int, str, int, int], ...]:
    return tuple(
        sorted(
            (cell.row, cell.col, cell.text, cell.rowspan, cell.colspan)
            for cell in grid.cells
        )
    )


def _trusted_bbox(bbox: BBox, page_bbox: BBox) -> bool:
    page_area = _area(page_bbox)
    if page_area <= 0:
        return False
    return _area(bbox) < page_area * BBOX_TRUST_AREA_RATIO and bbox != page_bbox


# ── Markdown 回退路径 ───────────────────────────────────────────────────


def _markdown_blocks(
    *,
    page_no: int,
    markdown: str,
    slots: Sequence[TableSlot],
    page_bbox: BBox,
    demoted_spans: Sequence[tuple[int, int]] = (),
    dropped_lines: frozenset[str] = frozenset(),
) -> list[Block]:
    lines = markdown.splitlines()
    slots, consumed = _markdown_captions(lines, slots)
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
            paragraph.extend(_demoted_text(lines[index:demoted_end]))
            index = demoted_end
            continue
        slot = by_start.get(index)
        if slot is not None:
            flush()
            blocks.append(_table_block(page_no, slot))
            index = slot.span[1] if slot.span is not None else index + 1
            continue
        if index in consumed:
            index += 1
            continue
        if _norm_header(lines[index]) in dropped_lines:
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
        if not lines[index].strip():
            flush()
            index += 1
            continue
        paragraph.append(lines[index].rstrip())
        index += 1
    flush()
    blocks.extend(_table_block(page_no, slot) for slot in trailing)
    return blocks


def _markdown_captions(
    lines: Sequence[str], slots: Sequence[TableSlot]
) -> tuple[list[TableSlot], set[int]]:
    consumed: set[int] = set()
    updated: list[TableSlot] = []
    for slot in slots:
        if slot.span is None:
            updated.append(slot)
            continue
        found = _caption_above_lines(lines, slot.span[0])
        if found is not None:
            caption, index = found
            consumed.add(index)
            slot = replace(slot, caption=caption)
        updated.append(slot)
    return updated, consumed


def _caption_above_lines(
    lines: Sequence[str], start: int
) -> tuple[str, int] | None:
    for offset in range(1, CAPTION_SEARCH_DEPTH + 1):
        index = start - offset
        if index < 0:
            return None
        text = lines[index].strip()
        if not text:
            continue
        return (text, index) if _is_caption(text) else None
    return None


# ── 坐标项路径 ──────────────────────────────────────────────────────────


def _item_blocks(
    *,
    page_no: int,
    markdown: str,
    items: Sequence[PositionedText],
    slots: Sequence[TableSlot],
    page_bbox: BBox,
    dropped_lines: frozenset[str] = frozenset(),
) -> tuple[list[Block], list[str]]:
    headings = _heading_entries(markdown)
    remaining = [item for item in items if not _inside_any(item, slots)]
    lines = _cluster_lines(remaining)
    lines, consumed, slots = _line_captions(lines, slots)
    median_width = _median_width(lines)
    matched: set[int] = set()
    blocks: list[Block] = []
    paragraph: list[_Line] = []
    warnings: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        blocks.append(
            Block(
                kind="text",
                page_no=page_no,
                bbox=_union_bbox([line.bbox for line in paragraph]),
                text=_join_lines([line.text for line in paragraph]),
            )
        )
        paragraph.clear()

    for line in lines:
        if line.norm in consumed:
            continue
        if _norm_header(line.text) in dropped_lines:
            flush()
            continue
        heading_index = _match_heading(line.norm, headings, matched)
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
        if paragraph and _starts_new_paragraph(
            paragraph[-1], line, median_width
        ):
            flush()
        paragraph.append(line)
    flush()
    blocks.extend(_table_block(page_no, slot) for slot in slots)
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


def _heading_entries(markdown: str) -> list[tuple[int, str, str]]:
    entries: list[tuple[int, str, str]] = []
    for line in markdown.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match is not None:
            text = match.group(2).strip()
            entries.append((len(match.group(1)), text, _norm(text)))
    return entries


def _match_heading(
    norm: str,
    headings: Sequence[tuple[int, str, str]],
    matched: set[int],
) -> int | None:
    for index, (_level, _text, heading_norm) in enumerate(headings):
        if index not in matched and heading_norm == norm:
            return index
    return None


def _cluster_lines(items: Sequence[PositionedText]) -> list[_Line]:
    ordered = sorted(items, key=lambda item: (-(item.y + item.height / 2), item.x))
    lines: list[_Line] = []
    for item in ordered:
        center = item.y + item.height / 2
        if lines:
            last = lines[-1]
            tolerance = max(2.0, max(last.height, item.height) * LINE_TOLERANCE_RATIO)
            if abs(last.center - center) <= tolerance:
                last.items.append(item)
                last.center = (last.center * (len(last.items) - 1) + center) / len(
                    last.items
                )
                last.x0 = min(last.x0, item.x)
                last.x1 = max(last.x1, item.x + item.width)
                last.height = max(last.height, item.height)
                continue
        lines.append(
            _Line(
                center=center,
                height=item.height,
                items=[item],
                x0=item.x,
                x1=item.x + item.width,
            )
        )
    return lines


def _line_captions(
    lines: Sequence[_Line], slots: Sequence[TableSlot]
) -> tuple[list[_Line], set[str], list[TableSlot]]:
    consumed: set[str] = set()
    updated: list[TableSlot] = []
    for slot in slots:
        found = _caption_above_table(lines, slot.bbox)
        if found is not None:
            consumed.add(found.norm)
            slot = replace(slot, caption=found.text.strip())
        updated.append(slot)
    return list(lines), consumed, updated


def _caption_above_table(
    lines: Sequence[_Line], bbox: BBox
) -> _Line | None:
    top = bbox[3]
    candidates = [
        line
        for line in lines
        if line.center > top
        and line.center - top <= max(20.0, line.height * 1.6)
        and _is_caption(line.text)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda line: line.center - top)


def _starts_new_paragraph(
    previous: _Line, current: _Line, median_width: float
) -> bool:
    if previous.width < median_width * PARAGRAPH_SHORT_RATIO:
        return True
    gap = previous.center - current.center
    if gap > max(previous.height, current.height) * PARAGRAPH_GAP_RATIO:
        return True
    return bool(previous.text.strip()) and previous.text.strip()[-1] in SENTENCE_END


def _median_width(lines: Sequence[_Line]) -> float:
    widths = [line.width for line in lines if line.width > 0]
    return statistics.median(widths) if widths else 0.0


def _join_lines(texts: Sequence[str]) -> str:
    result = ""
    for text in texts:
        piece = normalize_cjk_text(text).strip()
        if not piece:
            continue
        if result and _needs_space(result[-1], piece[0]):
            result += " "
        result += piece
    return result


def _needs_space(left: str, right: str) -> bool:
    return left.isascii() and left.isalnum() and right.isascii() and right.isalnum()


def _table_block(page_no: int, slot: TableSlot) -> Block:
    return Block(kind="table", page_no=page_no, bbox=slot.bbox, table=slot)


# ── 通用 ────────────────────────────────────────────────────────────────


def _page_bbox(items: Sequence[PositionedText]) -> BBox:
    if not items:
        return (0.0, 0.0, 1.0, 1.0)
    return (
        min(item.x for item in items),
        min(item.y for item in items),
        max(item.x + item.width for item in items),
        max(item.y + item.height for item in items),
    )


def _union_bbox(boxes: Sequence[BBox]) -> BBox:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _intersection_area(left: BBox, right: BBox) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    if x0 >= x1 or y0 >= y1:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _overlap_ratio(bbox: BBox, other: BBox) -> float:
    smaller = min(_area(bbox), _area(other))
    if smaller <= 0:
        return 0.0
    return _intersection_area(bbox, other) / smaller


def _inside_any(item: PositionedText, slots: Sequence[TableSlot]) -> bool:
    x = item.x + item.width / 2
    y = item.y + item.height / 2
    return any(
        slot.bbox[0] <= x <= slot.bbox[2] and slot.bbox[1] <= y <= slot.bbox[3]
        for slot in slots
    )


def _is_caption(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > CAPTION_MAX_CHARS:
        return False
    if stripped.startswith("#"):
        return False
    return _CAPTION_RE.search(stripped) is not None


def _is_prose_table(slot: TableSlot) -> bool:
    """L0-only 表若首行是长句正文，视为 pdf-inspector 伪表格。"""
    if slot.source != "markdown":
        return False
    first_row = [cell for cell in slot.grid.cells if cell.row == 0]
    if not first_row:
        return False
    joined = "".join(cell.text for cell in first_row)
    if not any(mark in joined for mark in SENTENCE_END):
        return False
    return (
        any(len(cell.text.strip()) >= PROSE_CELL_MIN_CHARS for cell in first_row)
        or sum(len(cell.text.strip()) for cell in first_row) >= PROSE_ROW_MIN_CHARS
    )


def _demoted_text(lines: Sequence[str]) -> list[str]:
    """把伪表格行还原成纯文本（去管道与分隔行）。"""
    texts: list[str] = []
    for line in lines:
        stripped = line.strip().strip("|")
        cells = [cell.strip() for cell in stripped.split("|")]
        if cells and all(
            _SEPARATOR_CELL_RE.match(cell) for cell in cells if cell
        ):
            continue
        joined = " ".join(cell for cell in cells if cell)
        if joined:
            texts.append(joined)
    return texts


def _running_headers(pages: Sequence[MergedPage]) -> frozenset[str]:
    """跨页重复的 <u>…</u> 行视为页眉，按页计数 ≥3 次才剔除。"""
    counter: Counter[str] = Counter()
    for page in pages:
        seen: set[str] = set()
        for line in page.markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("<u>") and stripped.endswith("</u>"):
                seen.add(_norm_header(stripped))
        counter.update(seen)
    return frozenset(
        text for text, count in counter.items() if count >= RUNNING_HEADER_MIN_PAGES
    )


def _norm_header(text: str) -> str:
    return _norm(re.sub(r"</?u>", "", text))


def _norm(text: str) -> str:
    return "".join(text.split())


def _table_id(index: int) -> str:
    return f"t{index:0{_TABLE_ID_WIDTH}d}"
