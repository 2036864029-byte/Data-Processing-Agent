"""坐标项行聚类：把 PositionedText 聚成视觉行。

PDF 常把带基线偏移的短数字拆成独立文本项（`(1)`、`21`、`8`），
按中心距离聚类会得到 `1 答：（）` 这类错位；`attach_inline_numbers`
按“数字 x 落在哪一行的列间隙”把它们挂回去。
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from dataagent.pipeline.quality.punctuation import SENTENCE_END
from dataagent.pipeline.structure.models import BBox
from dataagent.pipeline.table.geometry import PositionedText

LINE_TOLERANCE_RATIO: Final[float] = 0.6
PARAGRAPH_SHORT_RATIO: Final[float] = 0.85
PARAGRAPH_GAP_RATIO: Final[float] = 1.6

_NUMERIC_TOKEN: Final[re.Pattern[str]] = re.compile(r"^\d{1,3}$")
_INLINE_LINE_RANGE: Final[float] = 1.6
_INLINE_GAP_TOLERANCE: Final[float] = 1.5


@dataclass(slots=True)
class Line:
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
        return norm_text(self.text)

    @property
    def bbox(self) -> BBox:
        return (
            min(item.x for item in self.items),
            min(item.y for item in self.items),
            max(item.x + item.width for item in self.items),
            max(item.y + item.height for item in self.items),
        )


def cluster_lines(items: Sequence[PositionedText]) -> list[Line]:
    ordered = sorted(items, key=lambda item: (-(item.y + item.height / 2), item.x))
    lines: list[Line] = []
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
            Line(
                center=center,
                height=item.height,
                items=[item],
                x0=item.x,
                x1=item.x + item.width,
            )
        )
    return attach_inline_numbers(lines)


def attach_inline_numbers(lines: list[Line]) -> list[Line]:
    """把带基线偏移的短数字挂回 x 间隙所在的行。

    PDF 常把 `(1)`、`21`、`8` 等数字作为独立文本项，其 y 中心与正文行
    相差几磅；按中心距离聚类会把它们拆成单独一行，导致
    `1 答：（）` / `造 21 8 成名…` 这类错位。这里按“数字 x 落在哪一行的
    列间隙里”重新归属，再交给 `Line.text` 按 x 排序。
    """
    tokens: list[PositionedText] = []
    kept: list[Line] = []
    for line in lines:
        regular = [
            item for item in line.items if not _NUMERIC_TOKEN.match(item.text.strip())
        ]
        tokens.extend(
            item for item in line.items if _NUMERIC_TOKEN.match(item.text.strip())
        )
        if not regular:
            continue
        line.items = regular
        line.x0 = min(item.x for item in regular)
        line.x1 = max(item.x + item.width for item in regular)
        kept.append(line)

    for token in tokens:
        token_center = token.y + token.height / 2
        best: tuple[float, float, Line] | None = None
        for line in kept:
            if (
                abs(line.center - token_center)
                > max(line.height, token.height) * _INLINE_LINE_RANGE
            ):
                continue
            gap = gap_distance(line, token)
            if gap is None:
                continue
            candidate = (gap, abs(line.center - token_center), line)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
        if best is None:
            kept.append(
                Line(
                    center=token_center,
                    height=token.height,
                    items=[token],
                    x0=token.x,
                    x1=token.x + token.width,
                )
            )
            continue
        line = best[2]
        line.items.append(token)
        line.x0 = min(line.x0, token.x)
        line.x1 = max(line.x1, token.x + token.width)
        line.height = max(line.height, token.height)
    return kept


def gap_distance(line: Line, token: PositionedText) -> float | None:
    """数字 x 落在某行相邻文本项的间隙内时返回距离，否则 None。"""
    spans = sorted(
        (item.x, item.x + item.width)
        for item in line.items
        if item is not token
    )
    if not spans:
        return None
    x = token.x
    if x <= spans[0][0] + _INLINE_GAP_TOLERANCE:
        return max(0.0, spans[0][0] - x)
    for left, right in zip(spans, spans[1:], strict=False):
        if left[1] - _INLINE_GAP_TOLERANCE <= x <= right[0] + _INLINE_GAP_TOLERANCE:
            return 0.0
    if x >= spans[-1][1] - _INLINE_GAP_TOLERANCE:
        return max(0.0, x - spans[-1][1])
    return None


def starts_new_paragraph(
    previous: Line, current: Line, median_width: float
) -> bool:
    if previous.width < median_width * PARAGRAPH_SHORT_RATIO:
        return True
    gap = previous.center - current.center
    if gap > max(previous.height, current.height) * PARAGRAPH_GAP_RATIO:
        return True
    return bool(previous.text.strip()) and previous.text.strip()[-1] in SENTENCE_END


def median_width(lines: Sequence[Line]) -> float:
    widths = [line.width for line in lines if line.width > 0]
    return statistics.median(widths) if widths else 0.0


def norm_text(text: str) -> str:
    """行匹配用的空白无关形态（只用于比较，不用于输出）。"""
    return "".join(text.split())
