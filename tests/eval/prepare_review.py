"""生成人工打分的对照查看器（B9 标注准备）。

为每个样本页渲染 PNG（pypdfium2）并抽取该页 Markdown，生成
`var/golden/review/index.html`：左页图、右产物、四维打分 + needs_manual；
页内打完后可一键导出打分明细（文件名由 `--export-name` 指定）。

空产物区分两类：
- `needs_ocr`：无文本层（invisible_text_layer 等），原生解析为空属预期；
- `suspected_garbled`：引擎判定疑似乱码而未输出 Markdown，查看器回退展示原生文本项。
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pdf_inspector
import pypdfium2 as pdfium
from _lib import GOLDEN_ROOT, read_csv, read_json, write_json

from dataagent.contracts.structure import DocumentStructure
from dataagent.pipeline.output.structure_view import to_markdown_view
from dataagent.pipeline.quality.numbering import repair_numbering
from dataagent.pipeline.quality.text_normalize import normalize_cjk_text
from dataagent.pipeline.task.factory import create_builtin_runner
from dataagent.platform.plugins.adapters.text_layer import probe_text_layer

REVIEW_ROOT = GOLDEN_ROOT / "review"
PARSED_ROOT = GOLDEN_ROOT / "parsed"
RENDER_SCALE = 1.6


def _collect_artifacts(
    rows: list[dict[str, str]], manifest: dict[str, dict[str, Any]]
) -> dict[tuple[str, int], dict[str, Any]]:
    """按文件跑当前流水线（含相邻页），取每页的最终 Markdown 与结构 JSON。"""
    del manifest
    by_file: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        by_file[str(row["file"])].add(int(row["page_no"]))

    artifacts: dict[tuple[str, int], dict[str, Any]] = {}
    for file_name, pages in sorted(by_file.items()):
        path = Path(file_name)
        if not path.is_file():
            continue
        window = sorted(
            {page for target in pages for page in (target - 1, target, target + 1) if page >= 1}
        )
        try:
            with TemporaryDirectory() as output_dir:
                runner = create_builtin_runner(output_dir=output_dir)
                outcome = runner.process_pages(
                    path, window, task_id="review-artifact"
                )
                md_path = next(
                    (Path(item) for item in outcome.outputs if item.endswith(".md")),
                    None,
                )
                full_markdown = (
                    md_path.read_text(encoding="utf-8")
                    if md_path is not None
                    else ""
                )
                json_path = next(
                    (
                        Path(item)
                        for item in outcome.outputs
                        if item.endswith(".json") and not item.endswith(".tables.json")
                    ),
                    None,
                )
                payload = (
                    json.loads(json_path.read_text(encoding="utf-8"))
                    if json_path is not None
                    else None
                )
                structure_payload = (
                    payload.get("structure")
                    if isinstance(payload, dict)
                    else None
                )
                structure = (
                    DocumentStructure.model_validate(structure_payload)
                    if isinstance(structure_payload, dict)
                    else None
                )
                for page in pages:
                    markdown = (
                        to_markdown_view(
                            structure, page_order=[page], fallback={}
                        )
                        if structure is not None
                        else ""
                    )
                    gap = False
                    if not markdown.strip():
                        markdown = _gap_section(full_markdown, page)
                        gap = bool(markdown.strip())
                    artifacts[(file_name, page)] = {
                        "markdown": markdown,
                        "json": _page_json(payload, page),
                        "status": outcome.status,
                        "gap_pages": outcome.gap_pages,
                        "gap": gap,
                    }
        except Exception as exc:
            for page in pages:
                artifacts[(file_name, page)] = {
                    "error": f"{type(exc).__name__}: {exc}"
                }
    return artifacts


def _page_json(payload: dict[str, Any] | None, page_no: int) -> str:
    if payload is None:
        return ""
    if "structure" not in payload:
        # 无结构树的旧格式产物：原样展示，避免注入空结构误导
        return json.dumps(payload, ensure_ascii=False, indent=2)
    structure = payload.get("structure") or {}
    pages = [
        page
        for page in structure.get("pages", [])
        if int(page.get("page_no", -1)) == page_no
    ]
    return json.dumps(
        {
            "meta": payload.get("meta"),
            "structure": {
                "doc_id": structure.get("doc_id", ""),
                "pages": pages,
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def _artifact_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]


_GAP_MARKER = re.compile(
    r"<!-- dataagent:gap page=(\d+) [^>]*-->\s*(?:>.*(?:\n|$))*"
)


def _gap_section(markdown: str, page_no: int) -> str:
    """从交付 Markdown 中取某页的缺口占位段（结构树会跳过 gap 页）。

    占位段只有注释行 + 紧随的引用行；不能一直取到下一个缺口标记，
    否则会把后续已还原页面的正文带进来。
    """
    for marker in _GAP_MARKER.finditer(markdown):
        if int(marker.group(1)) == page_no:
            return marker.group(0).strip()
    return ""


_TABLE_BLOCK = re.compile(r"(<table>.*?</table>)", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _markdown_to_html(markdown: str) -> str:
    """把交付 Markdown 转成浏览器可读 HTML：表格原样渲染，其余文本转义。"""
    parts: list[str] = []
    for chunk in _TABLE_BLOCK.split(markdown):
        if chunk.startswith("<table>"):
            parts.append(chunk)
        else:
            parts.append(_text_to_html(chunk))
    return "".join(parts)


def _text_to_html(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        match = _HEADING.match(lines[0].strip())
        if match is not None and len(lines) == 1:
            level = len(match.group(1))
            blocks.append(
                f"<h{level}>{html.escape(match.group(2))}</h{level}>"
            )
            continue
        blocks.append(
            "<p>" + "<br>".join(html.escape(line) for line in lines) + "</p>"
        )
    return "".join(blocks)


def prepare(
    *,
    sheet_path: Path | None = None,
    review_root: Path | None = None,
    parsed_root: Path | None = None,
    export_name: str = "annotations_filled.csv",
) -> dict[str, Any]:
    sheet_path = sheet_path or GOLDEN_ROOT / "human_sheet.csv"
    review_root = review_root or REVIEW_ROOT
    parsed_root = parsed_root or PARSED_ROOT
    rows = read_csv(sheet_path)
    manifest = {
        row["sample_id"]: row for row in read_json(GOLDEN_ROOT / "manifest.json")
    }
    images = review_root / "images"
    images.mkdir(parents=True, exist_ok=True)
    parsed_root.mkdir(parents=True, exist_ok=True)
    for stale in parsed_root.glob("*.md"):
        stale.unlink()
    for stale in images.glob("*.png"):
        stale.unlink()

    entries: list[dict[str, Any]] = []
    artifacts = _collect_artifacts(rows, manifest)
    for row in rows:
        sample_id = row["sample_id"]
        file_path = Path(row["file"])
        if not file_path.exists():
            continue
        page_index = int(row["page_no"]) - 1
        raw_markdown = _extract_page_markdown(file_path, page_index)
        meta = manifest.get(sample_id, {})
        status, fallback_display, banner = _diagnose(
            file_path, page_index, raw_markdown, meta
        )
        artifact = artifacts.get((row["file"], int(row["page_no"])), {})
        delivered = str(artifact.get("markdown") or "")
        if delivered.strip():
            display = delivered
            artifact_source = "delivered"
            if artifact.get("gap"):
                banner = (
                    "该页在最终交付中为缺口占位（未通过质检，正文不输出）；"
                    "请判断这种结果是否可用于自动化下游。"
                    + (f" {banner}" if banner else "")
                )
            else:
                banner = (
                    "右侧为最终交付物：表格由流水线路由并以 HTML 渲染"
                    "（处理窗口含相邻页，用于跨页表衔接）。"
                    + (f" {banner}" if banner else "")
                )
        else:
            display = repair_numbering(normalize_cjk_text(fallback_display))
            artifact_source = "raw"
            error = artifact.get("error")
            if error:
                banner = f"流水线处理失败，展示原始抽取：{error}"
        parsed_path = parsed_root / f"{sample_id}.md"
        parsed_path.write_text(display, encoding="utf-8")
        image_path = images / f"{sample_id}.png"
        _render_page(file_path, page_index, image_path)
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
                "markdown_html": _markdown_to_html(display),
                "json": str(artifact.get("json") or ""),
                "artifact_hash": _artifact_hash(display),
                "artifact_source": artifact_source,
                "status": status,
                "banner": banner,
                "table_rows": meta.get("table_rows", ""),
                "table_cols": meta.get("table_cols", ""),
                "table_cells": meta.get("table_cells", ""),
            }
        )

    write_json(review_root / "index.json", entries)
    (review_root / "index.html").write_text(
        _render_html(entries, export_name=export_name), encoding="utf-8"
    )
    return {"samples": len(entries), "viewer": str(review_root / "index.html")}


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
    kind = str(row.get("text_layer") or "")
    if kind in {"invisible_text_layer", "garbled_text_layer"}:
        try:
            probed = probe_text_layer(path, pages=[page_index + 1]).pages[0]
        except Exception:
            probed = None
        if probed is not None and probed.usable:
            text = probed.text.replace("\r\n", "\n").replace("\r", "\n").strip()
            banner = (
                "pdf-inspector 未输出 Markdown；已通过 PDFium 恢复文本层"
                f"（{kind}），以下为恢复文本。"
            )
            return "text_layer_recovered", text + ("\n" if text else ""), banner
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


def _render_html(
    entries: list[dict[str, Any]], *, export_name: str = "annotations_filled.csv"
) -> str:
    cards = "\n".join(_card(entry) for entry in entries)
    digest = hashlib.sha256(
        "".join(entry.get("artifact_hash", "") for entry in entries).encode("utf-8")
    ).hexdigest()[:8]
    state_key = (
        "dataagent_golden_scores_"
        + re.sub(r"\W+", "_", export_name)
        + "_"
        + digest
    )
    return (
        _TEMPLATE.replace("__CARDS__", cards)
        .replace("__COUNT__", str(len(entries)))
        .replace("__EXPORT_NAME__", export_name)
        .replace("__STATE_KEY__", state_key)
    )


def _card(entry: dict[str, Any]) -> str:
    sample_id = html.escape(entry["sample_id"])
    category = html.escape(entry["category"])
    source = html.escape(entry["source"])
    file_name = html.escape(Path(entry["file"]).name)
    markdown_html = entry["markdown_html"]
    markdown_source = html.escape(entry["markdown"])
    json_text = html.escape(entry["json"] or "{}")
    artifact_hash = html.escape(entry.get("artifact_hash", ""))
    artifact_source = html.escape(entry.get("artifact_source", "raw"))
    banner = html.escape(entry["banner"])
    size = ""
    if entry["table_cells"] not in ("", None, "0"):
        size = (
            f'<span class="muted">表格 {entry["table_rows"]}×{entry["table_cols"]}'
            f'（{entry["table_cells"]} cell）</span>'
        )
    banner_html = f'<div class="banner">{banner}</div>' if banner else ""
    table_score = ""
    if entry["category"] in {"single_table", "cross_page_table"}:
        table_score = (
            '<label>表格专项 <select data-dim="dim_table">'
            f"{_options()}</select>"
            '<span class="muted">仅表格页；不计入总分</span></label>'
        )
    return f"""
<section class="card" data-sample="{sample_id}" data-category="{category}"
         data-artifact="{artifact_hash}">
  <header>
    <strong>{sample_id}</strong>
    <span class="tag">{category}</span>
    <span class="tag status-{html.escape(entry["status"])}">{html.escape(entry["status"])}</span>
    <span class="tag">artifact:{artifact_source}</span>
    <span class="muted">{source} · {file_name} · 第 {entry["page_no"]} 页
      · hash {artifact_hash}</span>
    {size}
  </header>
  <div class="body">
    <div class="pane"><img src="{entry["image"]}" alt="{sample_id}"></div>
    <div class="pane">
      {banner_html}
      <div class="tabs">
        <button class="tab active" data-tab="render">最终产物</button>
        <button class="tab" data-tab="source">Markdown 源码</button>
        <button class="tab" data-tab="json">JSON</button>
      </div>
      <div class="tabbody active" data-tab="render">{markdown_html}</div>
      <div class="tabbody" data-tab="source"><pre>{markdown_source}</pre></div>
      <div class="tabbody" data-tab="json"><pre>{json_text}</pre></div>
    </div>
  </div>
  <div class="scores">
    <label>文本还原 <select data-dim="dim_text">{_options()}</select></label>
    <label>结构还原 <select data-dim="dim_structure">{_options()}</select>
      <span class="muted">层级/段落/列表/表格行列/顺序</span></label>
    <label>语义还原 <select data-dim="dim_semantic">{_options()}</select></label>
    <label>版式还原 <select data-dim="dim_layout">{_options()}</select>
      <span class="muted">块位置/分栏/bbox</span></label>
    {table_score}
    <label class="manual"><input type="checkbox" data-dim="needs_manual"> 需要人工介入</label>
    <input class="notes" data-dim="error_notes" placeholder="问题备注（勾选需介入时必填）">
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
<title>DataAgent 黄金集标注（__COUNT__ 页）</title>
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
  .status-text_layer_recovered { background: #e8f5e9; color: #1b5e20; }
  .muted { color: #777; font-size: 12px; }
  .body { display: flex; gap: 12px; }
  .pane { flex: 1 1 50%; min-width: 0; }
  .pane img { max-width: 100%; border: 1px solid #ccc; }
  .pane pre { max-height: 70vh; overflow: auto; background: #fafafa; border: 1px solid #e5e5e5;
              padding: 10px; white-space: pre-wrap; word-break: break-word; font-size: 12px; }
  .tabs { display: flex; gap: 6px; margin-bottom: 6px; }
  .tab { padding: 3px 8px; border: 1px solid #ccc; background: #fff; color: #333;
         border-radius: 3px; cursor: pointer; font-size: 12px; }
  .tab.active { background: #2f6fed; color: #fff; border-color: #2f6fed; }
  .tabbody { display: none; max-height: 70vh; overflow: auto; background: #fafafa;
             border: 1px solid #e5e5e5; padding: 10px; }
  .tabbody.active { display: block; }
  .tabbody pre { margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 12px; }
  .tabbody table { border-collapse: collapse; font-size: 12px; }
  .tabbody th, .tabbody td { border: 1px solid #bbb; padding: 3px 6px; vertical-align: top; }
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
  <button id="export">导出 __EXPORT_NAME__</button>
</div>
<div class="hint">
  对照左侧 PDF 页面图与右侧产物打分：四维各 0–3（3 最好，0 不可用），并标记是否需要人工介入。
  右侧默认展示<b>最终交付物</b>：表格是流水线路由后以 HTML 渲染的表（不是原始 Markdown 竖线表），
  可切换查看 Markdown 源码与结构 JSON；卡片上的 hash 是本次展示产物版本。
  空产物页会显示状态：<b>needs_ocr</b> 表示无文本层（需 OCR）；
  <b>suspected_garbled</b> 表示引擎判定疑似乱码，右侧回退展示原生文本项。
  打分自动保存在本浏览器；完成后点“导出”，把文件放到
  <code>var/golden/__EXPORT_NAME__</code>，
  然后运行
  <code>python tests/eval/import_annotations.py --filled
  var/golden/__EXPORT_NAME__</code>。
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
  <p><b>需要人工介入</b>：产物无法直接用于自动化下游（字段不可定位、内容不可用、
    关键行列/结构不可信）才勾“是”。简单表格若行列与字段可读、还原度足够，
    即使结构分偏低也可以不勾。</p>
  <p><b>表格专项（仅表格页）</b>：只评“这张表本身”——表头/列对齐/合并单元格/单元格内容完整性。
    它<b>不计入总分</b>；表格问题仍照常计入结构（行列与顺序）与版式（块位置/合并/分栏）。</p>
</details>
__CARDS__
<script>
const KEY = "__STATE_KEY__";
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
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    const card = btn.closest(".card");
    card.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b === btn));
    card.querySelectorAll(".tabbody").forEach(
      b => b.classList.toggle("active", b.dataset.tab === btn.dataset.tab)
    );
  });
});
document.getElementById("export").addEventListener("click", () => {
  for (const card of cards) {
    const e = state[card.dataset.sample] || {};
    if (e.needs_manual && !String(e.error_notes || "").trim()) {
      alert("样本 " + card.dataset.sample + " 勾选了“需要人工介入”，请填写问题备注");
      return;
    }
  }
  const header = ["sample_id","dim_text","dim_structure","dim_semantic","dim_layout",
                  "dim_table","needs_manual","error_notes","annotator","date","label_source",
                  "artifact_hash"];
  const lines = [header.join(",")];
  cards.forEach(card => {
    const e = state[card.dataset.sample] || {};
    lines.push([
      card.dataset.sample, e.dim_text ?? "", e.dim_structure ?? "",
      e.dim_semantic ?? "", e.dim_layout ?? "", e.dim_table ?? "",
      e.needs_manual ? "是" : "否",
      e.error_notes ?? "", "", "", "human", card.dataset.artifact ?? "",
    ].map(csvEscape).join(","));
  });
  const blob = new Blob(["\\ufeff" + lines.join("\\n")], {type: "text/csv;charset=utf-8"});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "__EXPORT_NAME__";
  link.click();
  URL.revokeObjectURL(link.href);
});
restore();
</script>
</html>
"""


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="生成人工打分对照查看器")
    parser.add_argument("--sheet", type=Path, default=GOLDEN_ROOT / "human_sheet.csv")
    parser.add_argument("--out", type=Path, default=REVIEW_ROOT)
    parser.add_argument("--parsed", type=Path, default=PARSED_ROOT)
    parser.add_argument("--export-name", default="annotations_filled.csv")
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                sheet_path=args.sheet,
                review_root=args.out,
                parsed_root=args.parsed,
                export_name=args.export_name,
            ),
            ensure_ascii=False,
        )
    )
