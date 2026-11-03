"""表格抽取离线验证。

两类检查：
1. 构造样本（有真值）：pdfplumber / L0 / L1 的 cell F1 对比；
2. 真实页（无单元格真值）：四栏对照（原图 / L0 / L1 / pdfplumber）+ 路由结果与验收原因。

用法：
    uv run python scripts/validate_tables.py
    uv run python scripts/validate_tables.py --pdf <path> --pages 24,25,60,86
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pdf_inspector
import pypdfium2 as pdfium

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.table.acceptance import coverage_of, evaluate_candidate
from dataagent.pipeline.table.anchor import project_grid
from dataagent.pipeline.table.geometry import (
    build_table_grid,
    detect_table_regions,
    items_for_page,
)
from dataagent.pipeline.table.markdown import markdown_tables
from dataagent.pipeline.table.render import to_markdown
from dataagent.pipeline.table.router import route_tables
from dataagent.platform.plugins.adapters.table_pdfplumber import PdfplumberTableSource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "eval"))

from _lib import GOLDEN_ROOT, parse_markdown_tables, read_json, table_cell_f1  # noqa: E402

OUTPUT = ROOT / "var" / "table_compare"
DEFAULT_PDF = ROOT / "doc" / "test_pdf" / "贵州茅台2024年年度报告全文.pdf"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="表格抽取离线验证")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--pages", default="24,25,60,86")
    parser.add_argument(
        "--constructed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否跑构造样本真值 F1",
    )
    args = parser.parse_args(argv)
    OUTPUT.mkdir(parents=True, exist_ok=True)

    if args.constructed:
        report = evaluate_constructed()
        (OUTPUT / "constructed_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report["means"], ensure_ascii=False, indent=2))

    if not args.pdf.exists():
        print(f"跳过真实页对照：找不到 {args.pdf}")
        return 0
    pages = [int(value) for value in args.pages.split(",") if value.strip()]
    sections = render_real_pages(args.pdf, pages)
    (OUTPUT / "index.html").write_text(_html(sections), encoding="utf-8")
    print(f"对照页：{OUTPUT / 'index.html'}")
    return 0


def evaluate_constructed() -> dict[str, Any]:
    manifest = read_json(GOLDEN_ROOT / "manifest.json")
    samples = [
        row
        for row in manifest
        if row.get("source") == "constructed" and row.get("has_table")
    ]
    results: list[dict[str, Any]] = []
    for row in samples:
        path = Path(str(row["file"]))
        truth = read_json(Path(str(row["truth_path"])))
        reference = _truth_matrix(truth)
        extraction = pdf_inspector.extract_pages_markdown(str(path))
        l0 = _combine(
            [
                table
                for page in extraction.pages
                for table in parse_markdown_tables(page.markdown or "")
            ]
        )
        raw_items = pdf_inspector.extract_text_with_positions(str(path))
        l1 = _combine(
            [
                _grid_matrix(grid)
                for page_no in range(1, len(extraction.pages) + 1)
                for grid in _geometry_grids(raw_items, page_no)
            ]
        )
        with PdfplumberTableSource(str(path)) as source:
            ruled = _combine(
                [
                    _grid_matrix(grid)
                    for page_no in range(1, len(extraction.pages) + 1)
                    for grid in source.extract_page(page_no)
                ]
            )
        results.append(
            {
                "sample_id": row["sample_id"],
                "category": row["category"],
                "l0_f1": round(table_cell_f1(reference, l0), 4),
                "l1_f1": round(table_cell_f1(reference, l1), 4),
                "ruled_f1": round(table_cell_f1(reference, ruled), 4),
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[result["category"]].append(result)
    means: dict[str, dict[str, float]] = {}
    for category, rows in sorted(grouped.items()):
        means[category] = {
            "samples": len(rows),
            "l0_f1": _mean(rows, "l0_f1"),
            "l1_f1": _mean(rows, "l1_f1"),
            "ruled_f1": _mean(rows, "ruled_f1"),
        }
    means["overall"] = {
        "samples": len(results),
        "l0_f1": _mean(results, "l0_f1"),
        "l1_f1": _mean(results, "l1_f1"),
        "ruled_f1": _mean(results, "ruled_f1"),
    }
    return {
        "means": means,
        "notes": (
            "cross_page 构造 PDF 的框线数比文本行少一条，表头行落在框线之外；"
            "pdfplumber 因此少一行表头（数据行全部命中），F1 0.9917 属夹具偏差。"
        ),
        "samples": results,
    }


def render_real_pages(pdf_path: Path, pages: list[int]) -> list[str]:
    raw_items = pdf_inspector.extract_text_with_positions(str(pdf_path))
    document = pdfium.PdfDocument(str(pdf_path))
    sections: list[str] = []
    try:
        with PdfplumberTableSource(str(pdf_path)) as source:
            for page_no in pages:
                markdown = (
                    pdf_inspector.extract_pages_markdown(
                        str(pdf_path), pages=[page_no - 1]
                    ).pages[0].markdown
                    or ""
                )
                items = items_for_page(raw_items, page_no)
                page_bbox = _page_bbox(items)
                l0 = markdown_tables(markdown, page_no=page_no, bbox=page_bbox)
                l1 = _geometry_grids(raw_items, page_no)
                ruled = source.extract_page(page_no)
                routing = route_tables(
                    markdown, items, page_no=page_no, ruled_tables=ruled
                )
                image_path = OUTPUT / f"page_{page_no}.png"
                _render_page(document, page_no - 1, image_path)
                sections.append(
                    _page_section(
                        page_no=page_no,
                        image=image_path.name,
                        l0=l0,
                        l1=l1,
                        ruled=ruled,
                        routing=routing,
                        items=items,
                        page_bbox=page_bbox,
                    )
                )
    finally:
        document.close()
    return sections


def _page_section(
    *,
    page_no: int,
    image: str,
    l0: list[TableGrid],
    l1: list[TableGrid],
    ruled: list[TableGrid],
    routing: Any,
    items: Any,
    page_bbox: tuple[float, float, float, float],
) -> str:
    decisions = _gate_decisions(ruled, l0, items, page_bbox)
    selected = "".join(
        f"<p><b>{html.escape(choice.source)}</b> · "
        f"{choice.grid.n_rows}×{choice.grid.n_cols} · "
        f"{html.escape(choice.reason)}</p>"
        f"<pre>{html.escape(to_markdown(choice.grid))}</pre>"
        for choice in routing.tables
    )
    return f"""
<section>
  <h2>第 {page_no} 页 · L0 {routing.markdown_count} / L1 {routing.geometry_count}
    / pdfplumber {routing.ruled_count} / 被拒 {html.escape(str(routing.rejected_ruled))}</h2>
  <div class="grid">
    <div class="pane"><h3>原图</h3><img src="{image}" alt="page {page_no}"></div>
    <div class="pane"><h3>L0 · pdf-inspector Markdown</h3>{_render_grids(l0)}</div>
    <div class="pane"><h3>L1 · 坐标聚类</h3>{_render_grids(l1)}</div>
    <div class="pane"><h3>pdfplumber · 有框线</h3>{_render_grids(ruled)}{decisions}</div>
  </div>
  <h3>路由选中</h3>
  {selected or "<p>未识别出表格</p>"}
</section>"""


def _render_grids(grids: list[TableGrid]) -> str:
    if not grids:
        return "<p class='muted'>无</p>"
    return "".join(
        f"<pre>{html.escape(to_markdown(grid))}</pre>" for grid in grids
    )


def _gate_decisions(
    ruled: list[TableGrid],
    l0: list[TableGrid],
    items: Any,
    page_bbox: tuple[float, float, float, float],
) -> str:
    projected = [project_grid(grid, items) for grid in l0]
    l0_grids = [
        item.grid if item is not None else grid
        for grid, item in zip(l0, projected, strict=True)
    ]
    lines: list[str] = []
    for index, grid in enumerate(ruled):
        reference = _best_reference(grid, l0_grids, projected)
        decision = evaluate_candidate(
            grid, reference, page_bbox=page_bbox if items else None
        )
        lines.append(
            f"<p class='muted'>候选 {index + 1}：{html.escape(decision.reason)} · "
            f"fill {decision.stats.filled_ratio:.2f} · "
            f"coverage {decision.stats.text_coverage} · "
            f"row_ratio {decision.stats.row_ratio}</p>"
        )
    return "".join(lines)


def _best_reference(
    ruled: TableGrid,
    l0_grids: list[TableGrid],
    projected: list[Any],
) -> TableGrid | None:
    best: tuple[float, float, int] | None = None
    for index, item in enumerate(projected):
        if item is None:
            continue
        overlap = _intersection_area(item.grid.bbox, ruled.bbox)
        if overlap <= 0:
            continue
        score = (coverage_of(l0_grids[index], [ruled], region=ruled.bbox), overlap, index)
        if best is None or score[:2] > best[:2]:
            best = score
    return None if best is None else l0_grids[best[2]]


def _geometry_grids(raw_items: Any, page_no: int) -> list[TableGrid]:
    grids: list[TableGrid] = []
    for region in detect_table_regions(
        items_for_page(raw_items, page_no), page_no=page_no
    ):
        try:
            grids.append(
                build_table_grid(
                    region.items,
                    page_no=page_no,
                    bbox=region.bbox,
                    confidence=region.confidence,
                )
            )
        except ValueError:
            continue
    return grids


def _truth_matrix(truth: dict[str, Any]) -> list[list[str]]:
    tables = truth.get("tables")
    if tables is None and truth["pages"][0].get("table"):
        tables = [truth["pages"][0]["table"]]
    if not tables:
        return []
    header = [str(cell) for cell in tables[0].get("header", [])]
    rows = [
        [str(cell) for cell in row]
        for table in tables
        for row in table.get("rows", [])
    ]
    return [header, *rows] if header else rows


def _grid_matrix(grid: TableGrid) -> list[list[str]]:
    matrix = [["" for _ in range(grid.n_cols)] for _ in range(grid.n_rows)]
    for cell in grid.cells:
        matrix[cell.row][cell.col] = cell.text
    return matrix


def _combine(matrices: list[list[list[str]]]) -> list[list[str]]:
    if not matrices:
        return []
    combined = [row[:] for row in matrices[0]]
    header = [cell.casefold() for cell in matrices[0][0]] if matrices[0] else []
    for matrix in matrices[1:]:
        if not matrix:
            continue
        candidate = [cell.casefold() for cell in matrix[0]]
        if candidate == header:
            combined.extend(row[:] for row in matrix[1:])
        else:
            combined.extend(matrix)
    return combined


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(float(row[key]) for row in rows) / len(rows), 4)


def _page_bbox(items: list[Any]) -> tuple[float, float, float, float]:
    if not items:
        return (0.0, 0.0, 1.0, 1.0)
    return (
        min(item.x for item in items),
        min(item.y for item in items),
        max(item.x + item.width for item in items),
        max(item.y + item.height for item in items),
    )


def _intersection_area(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    if x0 >= x1 or y0 >= y1:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _render_page(document: Any, page_index: int, output: Path) -> None:
    bitmap = document[page_index].render(scale=1.5)
    bitmap.to_pil().save(output, format="PNG")


def _html(sections: list[str]) -> str:
    return (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<title>表格抽取对照</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:20px;background:#f6f7f9}"
        "section{background:#fff;border:1px solid #ddd;border-radius:6px;"
        "margin:18px 0;padding:14px}"
        ".grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}"
        ".pane{min-width:0}h3{font-size:14px;margin:6px 0}"
        "img{max-width:100%;border:1px solid #ccc}"
        "pre{white-space:pre-wrap;font-size:11px;background:#fafafa;"
        "border:1px solid #eee;padding:8px;overflow:auto;max-height:420px}"
        ".muted{color:#666;font-size:12px}"
        "@media(max-width:1100px){.grid{grid-template-columns:1fr}}"
        "</style>"
        "<h1>表格抽取对照（原图 / L0 / L1 / pdfplumber）</h1>"
        + "".join(sections)
        + "</html>"
    )


if __name__ == "__main__":
    raise SystemExit(main())
