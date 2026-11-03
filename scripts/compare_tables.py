"""表格效果对照：原始页面图 vs 路由选中的 TableGrid 渲染。

用法：uv run python scripts/compare_tables.py --file <pdf> --pages 25,60,86
输出：var/table_compare/index.html（本地打开对照）
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

import pdf_inspector
import pypdfium2 as pdfium

from dataagent.pipeline.table.geometry import items_for_page
from dataagent.pipeline.table.render import to_markdown
from dataagent.pipeline.table.router import route_tables

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "var" / "table_compare"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="表格效果对照")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--pages", default="25,60,86")
    args = parser.parse_args(argv)
    pages = [int(value) for value in args.pages.split(",") if value.strip()]
    OUTPUT.mkdir(parents=True, exist_ok=True)

    raw_items = pdf_inspector.extract_text_with_positions(str(args.file))
    sections: list[str] = []
    for page_no in pages:
        markdown = (
            pdf_inspector.extract_pages_markdown(
                str(args.file), pages=[page_no - 1]
            ).pages[0].markdown
            or ""
        )
        routing = route_tables(
            markdown, items_for_page(raw_items, page_no), page_no=page_no
        )
        image_path = OUTPUT / f"page_{page_no}.png"
        _render_page(args.file, page_no - 1, image_path)
        cards = "".join(
            _table_card(index, choice) for index, choice in enumerate(routing.tables)
        ) or "<p class='muted'>未识别出表格</p>"
        sections.append(
            f"""
<section>
  <h2>第 {page_no} 页</h2>
  <div class="compare">
    <div class="pane"><img src="{image_path.name}" alt="page {page_no}"></div>
    <div class="pane">
      <p class="muted">L0 表 {routing.markdown_count} /
        L1 区域 {routing.geometry_count} / 选中 {len(routing.tables)}</p>
      {cards}
    </div>
  </div>
</section>"""
        )
        _print_tables(page_no, routing.tables)

    (OUTPUT / "index.html").write_text(_html(sections), encoding="utf-8")
    print(f"查看器：{OUTPUT / 'index.html'}")
    return 0


def _table_card(index: int, choice) -> str:
    rendered = html.escape(to_markdown(choice.grid))
    return f"""
<details open>
  <summary>表 {index + 1} · {choice.source} · score {choice.quality.score:.3f}
    · fill {choice.quality.cell_fill:.2f} · {choice.grid.n_rows}×{choice.grid.n_cols}</summary>
  <p class="muted">{html.escape(choice.reason)}</p>
  <pre>{rendered}</pre>
</details>"""


def _print_tables(page_no: int, choices) -> None:
    print(f"\n===== page {page_no} =====")
    for index, choice in enumerate(choices):
        print(
            f"[{index}] {choice.source} score={choice.quality.score:.3f} "
            f"{choice.grid.n_rows}x{choice.grid.n_cols}"
        )
        print(to_markdown(choice.grid))


def _render_page(path: Path, page_index: int, output: Path) -> None:
    document = pdfium.PdfDocument(str(path))
    try:
        bitmap = document[page_index].render(scale=1.5)
        bitmap.to_pil().save(output, format="PNG")
    finally:
        document.close()


def _html(sections: list[str]) -> str:
    return (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<title>表格效果对照</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:20px;background:#f6f7f9}"
        "section{background:#fff;border:1px solid #ddd;border-radius:6px;"
        "margin:18px 0;padding:14px}"
        ".compare{display:flex;gap:16px}.pane{flex:1;min-width:0}"
        "img{max-width:100%;border:1px solid #ccc}pre{white-space:pre-wrap;font-size:12px;"
        "background:#fafafa;border:1px solid #eee;padding:8px;overflow:auto}"
        ".muted{color:#666;font-size:12px}</style>"
        "<h1>表格效果对照（左：原页；右：路由选中输出）</h1>"
        + "".join(sections)
        + "</html>"
    )


if __name__ == "__main__":
    raise SystemExit(main())
