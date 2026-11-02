"""采集每页代理指标，供 extract_quality 标定（B9/T123 B 步）。

真实语料没有真值，指标来自 pdf-inspector 的文本项（近似口径，待 T125 端到端
管线接通后替换为系统 L1 指标）：

- `char_density`：字符数 / 文本项包围盒面积（×1000）
- `glyph_coverage`：1 − U+FFFD 占比
- `header_footer_ratio`：页高上下 8% 区域字符占比
- `bbox_coverage`：有宽高文本项占比

输出 `var/golden/metrics.csv`（sample_id 对齐 manifest）。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pdf_inspector
from _lib import GOLDEN_ROOT, read_json, write_csv

from dataagent.pipeline.quality.table_metrics import table_page_metrics

METRIC_FIELDS = (
    "sample_id",
    "category",
    "page_no",
    "char_count",
    "item_count",
    "bad_chars",
    "char_density",
    "glyph_coverage",
    "header_footer_ratio",
    "bbox_coverage",
    "table_cell_fill",
    "row_col_variance",
    "table_header_ok",
    "paragraph_flow",
    "line_length_variety",
    "clean_spacing",
    "numbering_ratio",
    "heading_ratio",
    "prose_ratio",
)


def collect() -> dict[str, int]:
    manifest = read_json(GOLDEN_ROOT / "manifest.json")
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in manifest:
        by_file[str(row["file"])].append(row)

    metrics: list[dict[str, Any]] = []
    for file_name, rows in by_file.items():
        items_by_page = _items_by_page(Path(file_name))
        for row in rows:
            page_no = int(row["page_no"])
            parsed = GOLDEN_ROOT / "parsed" / f"{row['sample_id']}.md"
            markdown = parsed.read_text(encoding="utf-8") if parsed.exists() else ""
            metrics.append(
                _metrics(row, items_by_page.get(page_no, []), markdown)
            )
    write_csv(GOLDEN_ROOT / "metrics.csv", metrics, METRIC_FIELDS)
    return {"samples": len(metrics)}


def _items_by_page(path: Path) -> dict[int, list[Any]]:
    grouped: dict[int, list[Any]] = defaultdict(list)
    for item in pdf_inspector.extract_text_with_positions(str(path)):
        grouped[int(item.page)].append(item)
    return dict(grouped)


def _metrics(
    row: dict[str, Any], items: list[Any], markdown: str
) -> dict[str, Any]:
    chars = sum(len(item.text or "") for item in items)
    bad = sum((item.text or "").count("�") for item in items)
    if items:
        xs = [float(item.x) for item in items]
        ys = [float(item.y) for item in items]
        widths = [float(item.width) for item in items]
        heights = [float(item.height) for item in items]
        area = max(1.0, (max(xs) - min(xs)) * (max(ys) - min(ys)))
        top_bottom = sum(
            len(item.text or "")
            for item in items
            if float(item.y) < 0.08 * 792 or float(item.y) > 0.92 * 792
        )
        bbox_ok = sum(1 for w, h in zip(widths, heights, strict=True) if w > 0 and h > 0)
    else:
        area, top_bottom, bbox_ok = 1.0, 0, 0

    table = table_page_metrics(markdown) if markdown else None
    return {
        "sample_id": row["sample_id"],
        "category": row["category"],
        "page_no": row["page_no"],
        "char_count": chars,
        "item_count": len(items),
        "bad_chars": bad,
        "char_density": round(chars / area * 1000, 4),
        "glyph_coverage": round(1 - bad / chars, 4) if chars else 0.0,
        "header_footer_ratio": round(top_bottom / chars, 4) if chars else 0.0,
        "bbox_coverage": round(bbox_ok / len(items), 4) if items else 0.0,
        "table_cell_fill": round(table.cell_fill, 4) if table else 1.0,
        "row_col_variance": round(table.column_variance, 4) if table else 0.0,
        "table_header_ok": 1.0 if (table is None or table.header_ok) else 0.0,
        **_text_features(markdown),
    }


_TERMINAL_PUNCTUATION = "。！？；：.!?;:）)】」』,，"
_NUMBERING = re.compile(
    r"^(第[一二三四五六七八九十百千0-9]+[条章节款]"
    r"|[一二三四五六七八九十]+、"
    r"|[（(]\d+[）)]"
    r"|[①-⑳]"
    r"|\d+[.、])"
)
_CJK_SPACE = re.compile(r"[一-鿿]\s+[一-鿿]")


def _text_features(markdown: str) -> dict[str, float]:
    """从产物文本估计结构类特征（与人工结构分同向的 0–1 指标）。"""
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not lines:
        return {
            "paragraph_flow": 0.0,
            "line_length_variety": 0.0,
            "clean_spacing": 0.0,
            "numbering_ratio": 0.0,
            "heading_ratio": 0.0,
            "prose_ratio": 0.0,
        }
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
    return {
        "paragraph_flow": round(1.0 - hard_wrap, 4),
        "line_length_variety": round(variety, 4),
        "clean_spacing": round(1.0 - spacing, 4),
        "numbering_ratio": round(numbering / len(prose), 4) if prose else 0.0,
        "heading_ratio": round(headings / len(lines), 4),
        "prose_ratio": round(1.0 - table_lines / len(lines), 4),
    }


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
