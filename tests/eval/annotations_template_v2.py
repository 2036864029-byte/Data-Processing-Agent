"""增量人工标注批次（B9 A 路径）：优先挑选结构风险高的页面。

第一批 60 页（六类各 10）已标注；本脚本从剩余**真实**页面中为每类再选 10 页，
其中 6 页按分类别风险分（表格填充/表头/列方差；文本断行/空格）优先，
4 页确定性抽样以保留负例。构造样本不进第二批（第一批已有且多为同模板克隆）。
输出 `var/golden/human_sheet_v2.csv`。

正例偏多才有足够样本来标定 QC 阈值；标注时要求表格页填 `dim_table`，
勾选“需要人工介入”必须写 `error_notes`（查看器已强制）。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pdf_inspector
from _lib import (
    CATEGORIES,
    DEFAULT_SEED,
    GOLDEN_ROOT,
    read_csv,
    read_json,
    write_csv,
)
from annotations_template import ANNOTATION_FIELDS

from dataagent.pipeline.quality.table_metrics import table_page_metrics
from dataagent.pipeline.quality.text_metrics import text_quality_metrics

PER_CATEGORY = 10
RISK_QUOTA = 6


def build() -> dict[str, Any]:
    manifest = read_json(GOLDEN_ROOT / "manifest.json")
    existing = {
        row["sample_id"] for row in read_csv(GOLDEN_ROOT / "human_sheet.csv")
    }
    selected: list[dict[str, Any]] = []
    stats: dict[str, dict[str, int]] = {}
    for category in CATEGORIES:
        candidates = [
            row
            for row in manifest
            if row["category"] == category
            and row["sample_id"] not in existing
            and row.get("source") == "real"
        ]
        scored = [
            (risk, row) for row in candidates if (risk := _risk(row)) is not None
        ]
        scored.sort(key=lambda item: (-item[0], item[1]["sample_id"]))
        risky = [row for _, row in scored[:RISK_QUOTA]]
        rest = [row for _, row in scored[RISK_QUOTA:]]
        rng = random.Random(f"{DEFAULT_SEED}:{category}")
        filler = sorted(
            rng.sample(rest, min(PER_CATEGORY - len(risky), len(rest))),
            key=lambda row: row["sample_id"],
        )
        chosen = sorted(
            [*risky, *filler], key=lambda row: row["sample_id"]
        )
        selected.extend(chosen)
        stats[category] = {"candidates": len(candidates), "selected": len(chosen)}
    write_csv(GOLDEN_ROOT / "human_sheet_v2.csv", selected, ANNOTATION_FIELDS)
    return {"selected": len(selected), "by_category": stats}


def _risk(row: dict[str, Any]) -> float | None:
    path = Path(str(row.get("file", "")))
    if not path.is_file():
        return None
    page_index = int(row["page_no"]) - 1
    try:
        result = pdf_inspector.extract_pages_markdown(
            str(path), pages=[page_index]
        )
    except Exception:
        return None
    markdown = result.pages[0].markdown if result.pages else ""
    category = str(row.get("category", ""))
    risk = 0.0
    if category in {"single_table", "cross_page_table"}:
        table = table_page_metrics(markdown) if "|" in markdown else None
        if table is None:
            risk += 1.5 + (0.0 if markdown.strip() else 1.0)
        else:
            risk += (1.0 - table.cell_fill) * 2.0
            risk += 0.0 if table.header_ok else 1.0
            risk += min(table.column_variance / 2.0, 1.0)
    elif markdown.strip():
        # 文本类页面才用文本质量；表格页的行占比低是正常的，不是风险
        text = text_quality_metrics(markdown)
        risk += (1.0 - text.clean_spacing) * 2.0
        risk += 1.0 - text.prose_ratio
        risk += 1.0 - text.paragraph_flow
    layer = str(row.get("text_layer", ""))
    if layer in {"garbled_text_layer", "pure_scan"}:
        risk += 2.0
    text_chars = int(row.get("text_chars") or 0)
    if text_chars < 50:
        risk += 1.0
    return round(risk, 4)


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
