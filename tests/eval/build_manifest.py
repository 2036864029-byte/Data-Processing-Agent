"""构建 200 页黄金集抽样清单（B9/T122 A 步）。

扫描 `doc/test_pdf/`（本地私有、不入库），按六类分层确定性抽样；
表格类只选**可识别为真实表格且足够长**的页（伪表格/短表不入选），
类别不足时用构造样本补齐；产出 manifest + 汇总到 `var/golden/`。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pdf_inspector
from _lib import (
    CATEGORIES,
    CONSTRUCTIBLE_CATEGORIES,
    DEFAULT_SEED,
    GOLDEN_ROOT,
    PageSignals,
    balanced_quotas,
    classify_page,
    classify_text_layer,
    deterministic_sample,
    mark_cross_page_tables,
    text_layer_summary,
    write_csv,
    write_json,
)
from construct import generate

from dataagent.pipeline.repair.layout import (
    TableStats,
    is_plausible_table,
    table_stats,
)
from dataagent.platform.plugins.adapters.text_layer import probe_text_layer

CORPUS_ROOT = Path("doc/test_pdf")
TARGET_PAGES = 200
LONG_SINGLE_MIN_ROWS = 3
LONG_SINGLE_MIN_CELLS = 10
LONG_CROSS_MIN_ROWS = 4
LONG_CROSS_MIN_CELLS = 12
MANIFEST_FIELDS = (
    "sample_id",
    "category",
    "source",
    "doc_id",
    "file",
    "page_no",
    "size_bytes",
    "has_table",
    "has_column",
    "needs_ocr",
    "encoding_bad",
    "table_rows",
    "table_cols",
    "table_cells",
    "ocr_reason",
    "text_layer",
    "text_chars",
    "bad_ratio",
    "truth_path",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def real_candidates() -> dict[str, list[dict[str, Any]]]:
    by_category: dict[str, list[dict[str, Any]]] = {
        category: [] for category in CATEGORIES
    }
    for path in sorted(CORPUS_ROOT.glob("*.pdf")):
        if path.stat().st_size == 0:
            continue
        doc_id = sha256_file(path)
        page_info = _page_info(path)
        marked = mark_cross_page_tables(
            {page_no: info[0] for page_no, info in page_info.items()}
        )
        for page_no, signals in marked.items():
            category = classify_page(signals)
            stats = page_info[page_no][1]
            text_info = page_info[page_no][2]
            if category == "single_table" and not _long_single(stats):
                continue
            if category == "cross_page_table" and not _long_cross(
                stats, page_info.get(page_no + 1, (None, None))[1]
            ):
                continue
            rows, cols, cells = _size_for(category, stats, page_info, page_no)
            by_category[category].append(
                {
                    "sample_id": "",
                    "category": category,
                    "source": "real",
                    "doc_id": doc_id,
                    "file": str(path),
                    "page_no": page_no,
                    "size_bytes": path.stat().st_size,
                    "has_table": str(signals.has_table).lower(),
                    "has_column": str(signals.has_column).lower(),
                    "needs_ocr": str(signals.needs_ocr).lower(),
                    "encoding_bad": str(signals.encoding_bad).lower(),
                    "table_rows": rows,
                    "table_cols": cols,
                    "table_cells": cells,
                    "ocr_reason": signals.ocr_reason or "",
                    "text_layer": text_info["text_layer"],
                    "text_chars": text_info["text_chars"],
                    "bad_ratio": text_info["bad_ratio"],
                    "truth_path": "",
                }
            )
    return by_category


def _page_info(
    path: Path,
) -> dict[int, tuple[PageSignals, TableStats | None, dict[str, Any]]]:
    extraction = pdf_inspector.extract_pages_markdown(str(path))
    table_pages = set(extraction.pages_with_tables)
    column_pages = set(extraction.pages_with_columns)
    ocr_pages = set(extraction.pages_needing_ocr)
    bad_pages: set[int] = set()
    for item in pdf_inspector.extract_text_with_positions(str(path)):
        if "�" in (item.text or ""):
            bad_pages.add(item.page)

    probe = None
    if ocr_pages:
        try:
            probe = probe_text_layer(str(path), pages=sorted(ocr_pages))
        except Exception:
            probe = None

    info: dict[int, tuple[PageSignals, TableStats | None, dict[str, Any]]] = {}
    for page in extraction.pages:
        page_no = page.page + 1
        markdown = page.markdown or ""
        plausible = [
            stats
            for stats in table_stats(markdown)
            if is_plausible_table(stats, page_chars=len(markdown))
        ]
        best = (
            max(plausible, key=lambda stats: stats.cells) if plausible else None
        )
        if page_no not in table_pages:
            best = None
        info[page_no] = (
            PageSignals(
                has_table=best is not None,
                has_column=page_no in column_pages,
                needs_ocr=page_no in ocr_pages,
                encoding_bad=page_no in bad_pages,
                ocr_reason=page.ocr_reason,
            ),
            best,
            _text_layer_info(
                page_no, markdown, probe, page.needs_ocr, page.ocr_reason
            ),
        )
    return info


def _text_layer_info(
    page_no: int,
    markdown: str,
    probe: Any,
    needs_ocr: bool,
    reason: str | None,
) -> dict[str, Any]:
    if needs_ocr:
        probed = probe.page(page_no) if probe is not None else None
        usable = bool(probed and probed.usable)
        chars = probed.quality.chars if probed is not None else 0
        bad_ratio = (
            probed.quality.bad_chars / chars if probed is not None and chars else 0.0
        )
        kind = classify_text_layer(
            needs_ocr=True,
            has_text=False,
            probe_usable=usable,
            reason=reason,
        )
    else:
        chars = len("".join(char for char in markdown if not char.isspace()))
        bad = markdown.count("�")
        bad_ratio = bad / chars if chars else 0.0
        kind = classify_text_layer(
            needs_ocr=False, has_text=bool(markdown.strip()), probe_usable=False
        )
    return {
        "text_layer": kind,
        "text_chars": chars,
        "bad_ratio": round(bad_ratio, 6),
    }


def _long_single(stats: TableStats | None) -> bool:
    if stats is None:
        return False
    return (
        stats.rows >= LONG_SINGLE_MIN_ROWS
        and stats.cells >= LONG_SINGLE_MIN_CELLS
    )


def _long_cross(stats: TableStats | None, next_stats: TableStats | None) -> bool:
    if stats is None or next_stats is None:
        return False
    combined_rows = stats.rows + next_stats.rows - 1
    return (
        combined_rows >= LONG_CROSS_MIN_ROWS
        and stats.columns >= 3
        and stats.cells + next_stats.cells - stats.columns >= LONG_CROSS_MIN_CELLS
    )


def _size_for(
    category: str,
    stats: TableStats | None,
    page_info: dict[int, tuple[PageSignals, TableStats | None]],
    page_no: int,
) -> tuple[int, int, int]:
    if stats is None:
        return 0, 0, 0
    if category == "cross_page_table":
        next_stats = page_info.get(page_no + 1, (None, None))[1]
        if next_stats is None:
            return stats.rows, stats.columns, stats.cells
        return (
            stats.rows + next_stats.rows - 1,
            stats.columns,
            stats.cells + next_stats.cells - stats.columns,
        )
    return stats.rows, stats.columns, stats.cells


def build() -> dict[str, Any]:
    quotas = balanced_quotas(TARGET_PAGES)
    real = real_candidates()
    constructed_path = GOLDEN_ROOT / "constructed"
    constructed = (
        generate()
        if not any(constructed_path.glob("*.pdf"))
        else _load_constructed()
    )

    selected: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"target_pages": TARGET_PAGES, "categories": {}}
    counter = 1
    for category in CATEGORIES:
        quota = quotas[category]
        candidates = _dedupe(real[category])
        chosen = deterministic_sample(
            candidates,
            quota,
            seed=DEFAULT_SEED + CATEGORIES.index(category),
            key=lambda row: (str(row["file"]), int(row["page_no"])),
        )
        for row in chosen:
            row["sample_id"] = f"R-{counter:04d}"
            counter += 1
        selected.extend(chosen)

        deficit = quota - len(chosen)
        constructed_added = 0
        if deficit > 0 and category in CONSTRUCTIBLE_CATEGORIES:
            extras = [row for row in constructed if row["category"] == category]
            extras = deterministic_sample(
                extras,
                deficit,
                seed=DEFAULT_SEED + 7,
                key=lambda row: str(row["sample_id"]),
            )
            selected.extend(extras)
            constructed_added = len(extras)
        summary["categories"][category] = {
            "available_real": len(candidates),
            "selected_real": len(chosen),
            "selected_constructed": constructed_added,
            "deficit": max(0, deficit - constructed_added),
        }

    selected.sort(key=lambda row: (row["category"], row["sample_id"]))
    write_json(GOLDEN_ROOT / "manifest.json", selected)
    write_csv(GOLDEN_ROOT / "manifest.csv", selected, MANIFEST_FIELDS)
    summary["total_selected"] = len(selected)
    summary["text_layer"] = text_layer_summary(selected)
    summary["real"] = sum(1 for row in selected if row["source"] == "real")
    summary["constructed"] = sum(
        1 for row in selected if row["source"] == "constructed"
    )
    write_json(GOLDEN_ROOT / "manifest_summary.json", summary)
    return summary


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row["file"]), int(row["page_no"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _load_constructed() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for truth_path in sorted((GOLDEN_ROOT / "constructed").glob("*.truth.json")):
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        pdf_path = Path(truth["pdf_path"])
        tables = truth.get("tables") or [
            truth["pages"][0].get("table") or {"header": [], "rows": []}
        ]
        table_rows = sum(
            len(table.get("rows", [])) + (1 if table.get("header") else 0)
            for table in tables
        )
        table_cols = max(
            (len(table.get("header", [])) for table in tables), default=0
        )
        rows.append(
            {
                "sample_id": truth["sample_id"],
                "category": truth["category"],
                "source": "constructed",
                "doc_id": "",
                "file": str(pdf_path),
                "page_no": 1,
                "size_bytes": pdf_path.stat().st_size,
                "has_table": str(
                    truth["category"] in {"single_table", "cross_page_table"}
                ).lower(),
                "has_column": str(truth["category"] == "multi_column").lower(),
                "needs_ocr": "false",
                "encoding_bad": "false",
                "table_rows": table_rows if table_cols else 0,
                "table_cols": table_cols,
                "table_cells": table_rows * table_cols,
                "ocr_reason": "",
                "text_layer": "present",
                "text_chars": len(
                    "".join(
                        line
                        for page in truth["pages"]
                        for line in page.get("text_lines", [])
                        if not line.isspace()
                    )
                ),
                "bad_ratio": 0.0,
                "truth_path": str(truth_path),
            }
        )
    return rows


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
