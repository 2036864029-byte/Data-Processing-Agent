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
from collections import defaultdict
from pathlib import Path
from typing import Any

import pdf_inspector
from _lib import GOLDEN_ROOT, read_json, write_csv

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
            metrics.append(_metrics(row, items_by_page.get(page_no, [])))
    write_csv(GOLDEN_ROOT / "metrics.csv", metrics, METRIC_FIELDS)
    return {"samples": len(metrics)}


def _items_by_page(path: Path) -> dict[int, list[Any]]:
    grouped: dict[int, list[Any]] = defaultdict(list)
    for item in pdf_inspector.extract_text_with_positions(str(path)):
        grouped[int(item.page)].append(item)
    return dict(grouped)


def _metrics(row: dict[str, Any], items: list[Any]) -> dict[str, Any]:
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
    }


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
