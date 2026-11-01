"""生成 60 页人工打分的对照查看器（B9 标注准备）。

为每个样本页渲染 PNG（pypdfium2）并抽取该页 Markdown，生成
`var/golden/review/index.html`：左页图、右产物、四维打分 + needs_manual；
页内打完后可一键导出 `annotations_filled.csv`。

空产物区分两类：
- `needs_ocr`：无文本层（invisible_text_layer 等），原生解析为空属预期；
- `suspected_garbled`：引擎判定疑似乱码而未输出 Markdown，查看器回退展示原生文本项。
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pdf_inspector
import pypdfium2 as pdfium
from _lib import GOLDEN_ROOT, read_csv, read_json, write_json

REVIEW_ROOT = GOLDEN_ROOT / "review"
PARSED_ROOT = GOLDEN_ROOT / "parsed"
RENDER_SCALE = 1.6


def prepare() -> dict[str, Any]:
    rows = read_csv(GOLDEN_ROOT / "human_sheet.csv")
    manifest = {
        row["sample_id"]: row for row in read_json(GOLDEN_ROOT / "manifest.json")
    }
    images = REVIEW_ROOT / "images"
    images.mkdir(parents=True, exist_ok=True)
    PARSED_ROOT.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for row in rows:
        sample_id = row["sample_id"]
        file_path = Path(row["file"])
        if not file_path.exists():
            continue
        page_index = int(row["page_no"]) - 1
        markdown = _extract_page_markdown(file_path, page_index)
        parsed_path = PARSED_ROOT / f"{sample_id}.md"
        parsed_path.write_text(markdown, encoding="utf-8")
        image_path = images / f"{sample_id}.png"
        _render_page(file_path, page_index, image_path)

        meta = manifest.get(sample_id, {})
        status, display, banner = _diagnose(file_path, page_index, markdown, meta)
        entries.append(
            {
                "sample_id": sample_id,
                "category": row["category"],
                "source": row["source"],
                "file": row["file"],
                "page_no": int(row["page_no"]),
                "image": f"images/{sample_id}.png",
                "parsed": str(parsed_path),
                "markdown": display,
                "status": status,
                "banner": banner,
                "table_rows": meta.get("table_rows", ""),
                "table_cols": meta.get("table_cols", ""),
                "table_cells": meta.get("table_cells", ""),
            }
        )

    write_json(REVIEW_ROOT / "index.json", entries)
    (REVIEW_ROOT / "index.html").write_text(_render_html(entries), encoding="utf-8")
    return {"samples": len(entries), "viewer": str(REVIEW_ROOT / "index.html")}


def _extract_page_markdown(path: Path, page_index: int) -> str:
    result = pdf_inspector.extract_pages_markdown(str(path), pages=[page_index])
    if not result.pages:
        return ""
    return result.pages[0].markdown or ""


def _diagnose(
    path: Path, page_index: int, markdown: str, row: dict[str, Any]
) -> tuple[str, str, str]:
    if markdown.strip():
        return "ok", markdown, ""
    reason = str(row.get("ocr_reason") or "")
    if reason == "suspected_garbled_text":
        text = _page_items_text(path, page_index)
        banner = (
            "pdf-inspector 判定该页疑似乱码（suspected_garbled_text），未输出 Markdown；"
            "以下为原生文本项回退展示，可能含替换符。"
        )
        return "suspected_garbled", text, banner
    banner = (
        f"该页无可提取文本层（{reason or 'needs_ocr'}），需 OCR；"
        "原生解析为空属于预期，不代表丢失。"
    )
    return "needs_ocr", "", banner


def _page_items_text(path: Path, page_index: int) -> str:
    page_no = page_index + 1
    grouped: dict[int, list[str]] = defaultdict(list)
    for item in pdf_inspector.extract_text_with_positions(str(path)):
        grouped[int(item.page)].append(item.text or "")
    return "\n".join(line for line in grouped.get(page_no, []) if line.strip())


def _render_page(path: Path, page_index: int, output: Path) -> None:
    document = pdfium.PdfDocument(str(path))
    try:
        page = document[page_index]
        bitmap = page.render(scale=RENDER_SCALE)
        bitmap.to_pil().save(output, format="PNG")
    finally:
        document.close()


def _render_html(entries: list[dict[str, Any]]) -> str:
    cards = "\n".join(_card(entry) for entry in entries)
    return _TEMPLATE.replace("__CARDS__", cards).replace(
        "__COUNT__", str(len(entries))
    )


def _card(entry: dict[str, Any]) -> str:
    sample_id = html.escape(entry["sample_id"])
    category = html.escape(entry["category"])
    source = html.escape(entry["source"])
    file_name = html.escape(Path(entry["file"]).name)
    markdown = html.escape(entry["markdown"])
    banner = html.escape(entry["banner"])
    size = ""
    if entry["table_cells"] not in ("", None, "0"):
        size = (
            f'<span class="muted">表格 {entry["table_rows"]}×{entry["table_cols"]}'
            f'（{entry["table_cells"]} cell）</span>'
        )
    banner_html = f'<div class="banner">{banner}</div>' if banner else ""
    return f"""
<section class="card" data-sample="{sample_id}" data-category="{category}">
  <header>
    <strong>{sample_id}</strong>
    <span class="tag">{category}</span>
    <span class="tag status-{html.escape(entry["status"])}">{html.escape(entry["status"])}</span>
    <span class="muted">{source} · {file_name} · 第 {entry["page_no"]} 页</span>
    {size}
  </header>
  <div class="body">
    <div class="pane"><img src="{entry["image"]}" alt="{sample_id}"></div>
    <div class="pane">{banner_html}<pre>{markdown}</pre></div>
  </div>
  <div class="scores">
    <label>文本还原 <select data-dim="dim_text">{_options()}</select></label>
    <label>结构还原 <select data-dim="dim_structure">{_options()}</select>
      <span class="muted">层级/段落/列表/表格行列/顺序</span></label>
    <label>语义还原 <select data-dim="dim_semantic">{_options()}</select></label>
    <label>版式还原 <select data-dim="dim_layout">{_options()}</select>
      <span class="muted">块位置/分栏/bbox</span></label>
    <label class="manual"><input type="checkbox" data-dim="needs_manual"> 需要人工介入</label>
    <input class="notes" data-dim="error_notes" placeholder="问题备注（可选）">
  </div>
</section>
"""


def _options() -> str:
    return '<option value="">-</option>' + "".join(
        f'<option value="{value}">{value}</option>' for value in range(4)
    )


_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>DataAgent 黄金集标注（60 页）</title>
<style>
  :root { color-scheme: light; }
  body { margin: 0; padding: 16px; background: #f6f7f9;
         font-family: system-ui, "Segoe UI", sans-serif; }
  .bar { position: sticky; top: 0; z-index: 9; display: flex; gap: 16px; align-items: center;
         padding: 10px 16px; background: #fff; border-bottom: 1px solid #ddd; }
  .bar h1 { font-size: 16px; margin: 0; }
  .bar .progress { color: #555; }
  button { padding: 6px 14px; border: 1px solid #2f6fed; background: #2f6fed; color: #fff;
           border-radius: 4px; cursor: pointer; }
  .hint { padding: 10px 16px; color: #444; background: #eef3ff; border-bottom: 1px solid #dbe6ff; }
  .rubric { margin: 12px 16px; padding: 10px 14px; background: #fff;
            border: 1px solid #ddd; border-radius: 6px; }
  .rubric summary { cursor: pointer; font-weight: 600; }
  .rubric p { font-size: 13px; color: #333; margin: 6px 0; }
  .rubric table { border-collapse: collapse; font-size: 12px; margin: 8px 0; }
  .rubric td, .rubric th { border: 1px solid #ddd; padding: 4px 8px; vertical-align: top; }
  .card { background: #fff; border: 1px solid #ddd; border-radius: 6px; margin: 16px;
          padding: 12px; }
  .card header { display: flex; gap: 10px; align-items: baseline; margin-bottom: 8px; }
  .tag { background: #eef3ff; color: #2f6fed; border-radius: 3px;
         padding: 1px 6px; font-size: 12px; }
  .status-needs_ocr { background: #fff4e5; color: #b26a00; }
  .status-suspected_garbled { background: #fdecea; color: #a61b1b; }
  .muted { color: #777; font-size: 12px; }
  .body { display: flex; gap: 12px; }
  .pane { flex: 1 1 50%; min-width: 0; }
  .pane img { max-width: 100%; border: 1px solid #ccc; }
  .pane pre { max-height: 70vh; overflow: auto; background: #fafafa; border: 1px solid #e5e5e5;
              padding: 10px; white-space: pre-wrap; word-break: break-word; font-size: 12px; }
  .banner { background: #fff4e5; border: 1px solid #f0c36d; color: #7a4a00;
            padding: 8px 10px; margin-bottom: 8px; font-size: 13px; border-radius: 4px; }
  .scores { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 10px; }
  .scores label { font-size: 13px; color: #333; }
  select { margin-left: 4px; }
  .notes { flex: 1 1 240px; padding: 5px 8px; border: 1px solid #ccc; border-radius: 4px; }
</style>
<div class="bar">
  <h1>黄金集标注</h1>
  <span class="progress">已填 <b id="filled">0</b> / __COUNT__</span>
  <button id="export">导出 annotations_filled.csv</button>
</div>
<div class="hint">
  对照左侧 PDF 页面图与右侧系统产物打分：四维各 0–3（3 最好，0 不可用），并标记是否需要人工介入。
  空产物页会显示状态：<b>needs_ocr</b> 表示无文本层（需 OCR）；
  <b>suspected_garbled</b> 表示引擎判定疑似乱码，
  右侧回退展示原生文本项。打分自动保存在本浏览器；完成后点“导出”，把文件放到
  <code>var/golden/annotations_filled.csv</code>，
  然后运行 <code>python tests/eval/import_annotations.py</code>。
</div>
<details class="rubric">
  <summary>评分标准（点击展开）：四维边界与 0–3 锚点</summary>
  <p><b>结构还原看逻辑</b>：阅读顺序、标题层级、段落/列表边界、表格行列结构。<br>
     <b>版式还原看位置</b>：文本块/表格在页面上的位置与分栏对应（bbox/列结构）。<br>
     “段落/列表边界丢了”属于结构扣分；“块位置/分栏错了”属于版式扣分。</p>
  <table>
    <tr><th>分</th><th>结构还原</th><th>版式还原</th></tr>
    <tr><td>3</td><td>层级/段落/列表/表格行列与顺序全对</td><td>块位置/分栏与页面一致</td></tr>
    <tr><td>2</td><td>少量段落合并，层级仍可辨认</td><td>轻微位置差异，顺序正确</td></tr>
    <tr><td>1</td><td>大量段落/列表边界丢失或标题平铺</td><td>明显错位或分栏被破坏</td></tr>
    <tr><td>0</td><td>阅读顺序错乱或表格结构不可用</td><td>无法把输出块对应到页面位置</td></tr>
  </table>
  <p>文本还原看字符完整性（错字/漏字/乱码）；语义还原看关键字段（金额/日期/编号/名称）。</p>
  <p>示例：文字全对、顺序对、只是（一）（二）被合并成一段 → 文本 3 / 结构 1–2 / 语义 3 / 版式 2–3；
     双栏交错或整页落入表格单元格 → 结构 1 / 版式 1。</p>
  <p><b>需要人工介入</b>：结构 ≤1，或关键字段因边界丢失无法可靠定位时勾“是”。</p>
</details>
__CARDS__
<script>
const KEY = "dataagent_golden_scores_v1";
const state = JSON.parse(localStorage.getItem(KEY) || "{}");
const cards = Array.from(document.querySelectorAll(".card"));

function save(sample) {
  const card = cards.find(c => c.dataset.sample === sample);
  const entry = state[sample] || {};
  card.querySelectorAll("[data-dim]").forEach(el => {
    if (el.type === "checkbox") entry[el.dataset.dim] = el.checked;
    else entry[el.dataset.dim] = el.value;
  });
  state[sample] = entry;
  localStorage.setItem(KEY, JSON.stringify(state));
  updateProgress();
}
function restore() {
  cards.forEach(card => {
    const entry = state[card.dataset.sample] || {};
    card.querySelectorAll("[data-dim]").forEach(el => {
      if (el.type === "checkbox") el.checked = !!entry[el.dataset.dim];
      else if (entry[el.dataset.dim] !== undefined) el.value = entry[el.dataset.dim];
      el.addEventListener("input", () => save(card.dataset.sample));
    });
  });
  updateProgress();
}
function updateProgress() {
  let filled = 0;
  cards.forEach(card => {
    const e = state[card.dataset.sample] || {};
    const dims = ["dim_text","dim_structure","dim_semantic","dim_layout"];
    if (dims.every(k => e[k] !== "" && e[k] !== undefined)) filled++;
  });
  document.getElementById("filled").textContent = filled;
}
function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}
document.getElementById("export").addEventListener("click", () => {
  const header = ["sample_id","dim_text","dim_structure","dim_semantic","dim_layout",
                  "needs_manual","error_notes","annotator","date","label_source"];
  const lines = [header.join(",")];
  cards.forEach(card => {
    const e = state[card.dataset.sample] || {};
    lines.push([
      card.dataset.sample, e.dim_text ?? "", e.dim_structure ?? "",
      e.dim_semantic ?? "", e.dim_layout ?? "", e.needs_manual ? "是" : "否",
      e.error_notes ?? "", "", "", "human",
    ].map(csvEscape).join(","));
  });
  const blob = new Blob(["\\ufeff" + lines.join("\\n")], {type: "text/csv;charset=utf-8"});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "annotations_filled.csv";
  link.click();
  URL.revokeObjectURL(link.href);
});
restore();
</script>
</html>
"""


if __name__ == "__main__":
    print(json.dumps(prepare(), ensure_ascii=False))
