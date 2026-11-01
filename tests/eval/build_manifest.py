"""构建 200 页黄金集抽样清单（B9/T122 A 步）。

扫描 `doc/test_pdf/`（本地私有、不入库），按六类分层确定性抽样；
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
    deterministic_sample,
    mark_cross_page_tables,
    write_csv,
    write_json,
)
from construct import generate

CORPUS_ROOT = Path("doc/test_pdf")
TARGET_PAGES = 200
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
    "truth_path",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def real_candidates() -> dict[str, list[dict[str, Any]]]:
    by_category: dict[str, list[dict[str, Any]]] = {category: [] for category in CATEGORIES}
    for path in sorted(CORPUS_ROOT.glob("*.pdf")):
        if path.stat().st_size == 0:
            continue
        doc_id = sha256_file(path)
        pages = _page_signals(path)
        marked = mark_cross_page_tables(pages)
        for page_no, signals in marked.items():
            category = classify_page(signals)
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
                    "truth_path": "",
                }
            )
    return by_category


def _page_signals(path: Path) -> dict[int, PageSignals]:
    extraction = pdf_inspector.extract_pages_markdown(str(path))
    table_pages = set(extraction.pages_with_tables)
    column_pages = set(extraction.pages_with_columns)
    ocr_pages = set(extraction.pages_needing_ocr)
    bad_pages: set[int] = set()
    for item in pdf_inspector.extract_text_with_positions(str(path)):
        if "�" in (item.text or ""):
            bad_pages.add(item.page)
    signals: dict[int, PageSignals] = {}
    for page in extraction.pages:
        page_no = page.page + 1
        signals[page_no] = PageSignals(
            has_table=page_no in table_pages,
            has_column=page_no in column_pages,
            needs_ocr=page_no in ocr_pages,
            encoding_bad=page_no in bad_pages,
        )
    return signals


def build() -> dict[str, Any]:
    quotas = balanced_quotas(TARGET_PAGES)
    real = real_candidates()
    constructed_path = GOLDEN_ROOT / "constructed"
    constructed = generate() if not any(constructed_path.glob("*.pdf")) else _load_constructed()

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
    manifest_path = GOLDEN_ROOT / "manifest.json"
    write_json(manifest_path, selected)
    write_csv(GOLDEN_ROOT / "manifest.csv", selected, MANIFEST_FIELDS)
    summary["total_selected"] = len(selected)
    summary["real"] = sum(1 for row in selected if row["source"] == "real")
    summary["constructed"] = sum(1 for row in selected if row["source"] == "constructed")
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
        rows.append(
            {
                "sample_id": truth["sample_id"],
                "category": truth["category"],
                "source": "constructed",
                "file": str(pdf_path),
                "page_no": 1,
                "size_bytes": pdf_path.stat().st_size,
                "truth_path": str(truth_path),
            }
        )
    return rows


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
