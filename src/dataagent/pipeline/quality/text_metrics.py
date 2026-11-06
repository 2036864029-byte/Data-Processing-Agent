"""产物文本的结构类代理指标（B9/T123）。

生产 L1 质量合成与黄金集标定共用这一份口径，避免“标定用一套、生产用另一套”。
所有指标 0–1，方向均为越大越好；输入是交付产物 Markdown。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TERMINAL_PUNCTUATION = "。！？；：.!?;:）)】」』,，"
_NUMBERING = re.compile(
    r"^(第[一二三四五六七八九十百千0-9]+[条章节款]"
    r"|[一二三四五六七八九十]+、"
    r"|[（(]\d+[）)]"
    r"|[①-⑳]"
    r"|\d+[.、])"
)
_CJK_SPACE = re.compile(r"[一-鿿]\s+[一-鿿]")


@dataclass(frozen=True, slots=True)
class TextQualityMetrics:
    paragraph_flow: float
    line_length_variety: float
    clean_spacing: float
    numbering_ratio: float
    heading_ratio: float
    prose_ratio: float

    def as_dict(self) -> dict[str, float]:
        return {
            "paragraph_flow": round(self.paragraph_flow, 4),
            "line_length_variety": round(self.line_length_variety, 4),
            "clean_spacing": round(self.clean_spacing, 4),
            "numbering_ratio": round(self.numbering_ratio, 4),
            "heading_ratio": round(self.heading_ratio, 4),
            "prose_ratio": round(self.prose_ratio, 4),
        }


def text_quality_metrics(markdown: str) -> TextQualityMetrics:
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not lines:
        return TextQualityMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    prose = [
        line
        for line in lines
        if not line.startswith("|") and not line.startswith("#")
    ]
    hard_wrap = (
        sum(1 for line in prose if line[-1] not in _TERMINAL_PUNCTUATION)
        / len(prose)
        if prose
        else 1.0
    )
    lengths = [len(line) for line in prose]
    mean = sum(lengths) / len(lengths) if lengths else 0.0
    variance = (
        sum((length - mean) ** 2 for length in lengths) / len(lengths)
        if lengths
        else 0.0
    )
    variety = min((variance**0.5) / mean, 1.0) if mean else 0.0
    cjk_spaces = len(_CJK_SPACE.findall(markdown))
    spacing = min(cjk_spaces / max(1, len(markdown)) * 100, 1.0)
    numbering = sum(1 for line in prose if _NUMBERING.match(line))
    headings = sum(
        1 for line in lines if line.startswith("#") or line.startswith("**")
    )
    table_lines = sum(1 for line in lines if line.startswith("|"))
    return TextQualityMetrics(
        paragraph_flow=1.0 - hard_wrap,
        line_length_variety=variety,
        clean_spacing=1.0 - spacing,
        numbering_ratio=numbering / len(prose) if prose else 0.0,
        heading_ratio=headings / len(lines),
        prose_ratio=1.0 - table_lines / len(lines),
    )
