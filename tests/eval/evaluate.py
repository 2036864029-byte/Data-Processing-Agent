"""构造样本自动评测（B9/T124 自动部分）。

对构造样本（已知真值）跑 pdf-inspector，计算：
- CER（文本还原）
- 阅读顺序 LCS 一致率（结构还原）
- 表格 cell F1（TEDS 代理）
- 字段 exact match（构建样本未定义字段时为 n/a）

门禁：CER ≤ 5%、阅读顺序 ≥ 88%、表格 F1 ≥ 0.80（仅表格类）。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pdf_inspector
from _lib import (
    GOLDEN_ROOT,
    character_error_rate,
    parse_markdown_tables,
    plain_text,
    read_json,
    reading_order_accuracy,
    table_cell_f1,
    text_layer_summary,
    write_json,
)
from recovery_probe import recovery_eval

CER_MAX = 0.05
READING_ORDER_MIN = 0.88
TABLE_F1_MIN = 0.80


def evaluate() -> dict[str, Any]:
    manifest = read_json(GOLDEN_ROOT / "manifest.json")
    samples = [row for row in manifest if row["source"] == "constructed"]
    results: list[dict[str, Any]] = []
    for row in samples:
        truth = read_json(Path(str(row["truth_path"])))
        extraction = pdf_inspector.extract_pages_markdown(str(row["file"]))
        markdown = "\n".join(page.markdown or "" for page in extraction.pages)
        text = plain_text(markdown)
        reference_text = "\n".join(
            line for page in truth["pages"] for line in page.get("text_lines", [])
        )
        tables = truth.get("tables")
        if tables is None and truth["pages"][0].get("table"):
            tables = [truth["pages"][0]["table"]]
        reference_grid = _reference_grid(tables) if tables else []
        parsed_tables = parse_markdown_tables(markdown)
        hypothesis_grid = _combine_tables(parsed_tables) if parsed_tables else []
        results.append(
            {
                "sample_id": row["sample_id"],
                "category": row["category"],
                "cer": round(character_error_rate(reference_text, text), 4),
                "reading_order": round(
                    reading_order_accuracy(truth["reading_order"], text), 4
                ),
                "table_f1": (
                    round(table_cell_f1(reference_grid, hypothesis_grid), 4)
                    if reference_grid
                    else None
                ),
            }
        )

    summary: dict[str, Any] = {"categories": {}, "gates": {}}
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_category[result["category"]].append(result)
    for category, rows in sorted(by_category.items()):
        summary["categories"][category] = {
            "samples": len(rows),
            "cer_mean": round(sum(row["cer"] for row in rows) / len(rows), 4),
            "reading_order_mean": round(
                sum(row["reading_order"] for row in rows) / len(rows), 4
            ),
            "table_f1_mean": (
                round(
                    sum(row["table_f1"] for row in rows if row["table_f1"] is not None)
                    / max(1, sum(1 for row in rows if row["table_f1"] is not None)),
                    4,
                )
                if any(row["table_f1"] is not None for row in rows)
                else None
            ),
        }

    all_rows = results
    summary["overall"] = {
        "samples": len(all_rows),
        "cer_mean": round(sum(row["cer"] for row in all_rows) / len(all_rows), 4),
        "reading_order_mean": round(
            sum(row["reading_order"] for row in all_rows) / len(all_rows), 4
        ),
    }
    cer_pass = all(row["cer"] <= CER_MAX for row in all_rows)
    order_pass = all(row["reading_order"] >= READING_ORDER_MIN for row in all_rows)
    table_pass = all(
        row["table_f1"] is None or row["table_f1"] >= TABLE_F1_MIN for row in all_rows
    )
    recovery = recovery_eval(cer_max=CER_MAX)
    summary["gates"] = {
        "cer_max": CER_MAX,
        "reading_order_min": READING_ORDER_MIN,
        "table_f1_min": TABLE_F1_MIN,
        "cer_pass": cer_pass,
        "reading_order_pass": order_pass,
        "table_pass": table_pass,
        "recovery_pass": recovery["pass"],
        "pass": cer_pass and order_pass and table_pass and recovery["pass"],
    }
    summary["samples"] = results
    summary["text_layer"] = text_layer_summary(manifest)
    summary["recovery_eval"] = recovery
    write_json(GOLDEN_ROOT / "eval_report.json", summary)
    return summary


def _reference_grid(tables: list[dict[str, Any]]) -> list[list[str]]:
    header = [str(cell) for cell in tables[0].get("header", [])]
    rows = [
        [str(cell) for cell in row]
        for table in tables
        for row in table.get("rows", [])
    ]
    return [header, *rows] if header else rows


def _combine_tables(tables: list[list[list[str]]]) -> list[list[str]]:
    if not tables:
        return []
    combined = [row[:] for row in tables[0]]
    header = [cell.casefold() for cell in tables[0][0]] if tables[0] else []
    for table in tables[1:]:
        if not table:
            continue
        candidate = [cell.casefold() for cell in table[0]]
        if candidate == header:
            combined.extend(row[:] for row in table[1:])
        else:
            combined.extend(table)
    return combined


if __name__ == "__main__":
    report = evaluate()
    print(json.dumps(report["overall"], ensure_ascii=False, indent=2))
    print(json.dumps(report["gates"], ensure_ascii=False, indent=2))
    sys.exit(0 if report["gates"]["pass"] else 1)
