"""生成人工标注模板与人工子集清单（B9/T122 标注准备）。

- `var/golden/annotations.csv`：200 页全量模板（已存在则不覆盖）；
- `var/golden/human_sheet.csv`：六类各 10 页=60 页人工打分子集（40 拟合 + 20 留出）；
- `var/golden/viewer.html`：并排查看入口（左：PDF 文件页码；右：产物路径占位）。
"""

from __future__ import annotations

import html
import json

from _lib import (
    CATEGORIES,
    GOLDEN_ROOT,
    HUMAN_PAGES_PER_CATEGORY,
    deterministic_sample,
    read_json,
    write_csv,
)

ANNOTATION_FIELDS = (
    "sample_id",
    "category",
    "source",
    "file",
    "page_no",
    "dim_text",
    "dim_structure",
    "dim_semantic",
    "dim_layout",
    "dim_table",
    "total",
    "needs_manual",
    "error_notes",
    "annotator",
    "date",
    "label_source",
)


def build() -> dict[str, int]:
    manifest = read_json(GOLDEN_ROOT / "manifest.json")
    annotations = GOLDEN_ROOT / "annotations.csv"
    if not annotations.exists():
        write_csv(annotations, manifest, ANNOTATION_FIELDS)

    human: list[dict[str, object]] = []
    for category in CATEGORIES:
        rows = sorted(
            (row for row in manifest if row["category"] == category),
            key=lambda row: row["sample_id"],
        )
        human.extend(
            deterministic_sample(
                rows,
                HUMAN_PAGES_PER_CATEGORY,
                key=lambda row: row["sample_id"],
            )
        )
    write_csv(GOLDEN_ROOT / "human_sheet.csv", human, ANNOTATION_FIELDS)
    _write_viewer(manifest)
    return {"annotations": len(manifest), "human": len(human)}


def _write_viewer(manifest: list[dict[str, object]]) -> None:
    rows = []
    for row in manifest:
        parsed = GOLDEN_ROOT / "parsed" / f"{row['sample_id']}.md"
        parsed_cell = (
            f'<a href="{html.escape(str(parsed))}">产物</a>' if parsed.exists() else "待生成"
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(row['sample_id']))}</td>"
            f"<td>{html.escape(str(row['category']))}</td>"
            f"<td>{html.escape(str(row['source']))}</td>"
            f"<td>{html.escape(str(row['file']))}</td>"
            f"<td>{row['page_no']}</td>"
            f"<td>{parsed_cell}</td>"
            "</tr>"
        )
    document = (
        "<!doctype html><meta charset='utf-8'><title>DataAgent 黄金集</title>"
        "<style>body{font-family:sans-serif;margin:24px}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}</style>"
        "<h1>黄金集样本清单</h1>"
        "<p>用 PDF 阅读器打开文件并跳到指定页，与产物并排对照打分。</p>"
        "<table><tr><th>样本</th><th>类别</th><th>来源</th><th>文件</th>"
        "<th>页码</th><th>产物</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    (GOLDEN_ROOT / "viewer.html").write_text(document, encoding="utf-8")


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))
