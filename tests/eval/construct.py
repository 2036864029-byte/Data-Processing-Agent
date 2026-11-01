"""构造样本生成（B9/T122 自动真值部分）。

用已知文本/表格生成最小 PDF，并写出真值 JSON：
- 单表页、跨页表、多栏页（分栏顺序）、纯文本页；
- 真值包含每页文本行、表格 header/rows、期望阅读顺序。

构造样本用于自动标定（CER / 阅读顺序 / 表格 cell F1），输出在 `var/golden/`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _lib import GOLDEN_ROOT, write_json

PAGE_W = 612
PAGE_H = 792


@dataclass(frozen=True, slots=True)
class TableSpec:
    x: float
    y: float
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    cell_w: float = 110
    cell_h: float = 22


@dataclass
class PageSpec:
    lines: list[tuple[float, float, int, str]] = field(default_factory=list)
    table: TableSpec | None = None


def build_pdf(pages: list[PageSpec]) -> bytes:
    objects: list[str] = []
    count = len(pages)
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(count))
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>")
    font_id = 3 + 2 * count
    for index, page in enumerate(pages):
        stream = _content(page)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {4 + index * 2} 0 R >>"
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    document = "%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(document)
    document += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        document += f"{offset:010d} 00000 n \n"
    document += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    )
    return document.encode("latin-1")


def _content(page: PageSpec) -> str:
    parts: list[str] = []
    for x, y, size, text in page.lines:
        parts.append(f"BT /F1 {size} Tf {x} {y} Td ({_escape(text)}) Tj ET")
    if page.table is not None:
        parts.extend(_table_content(page.table))
    return "\n".join(parts)


def _table_content(table: TableSpec) -> list[str]:
    parts: list[str] = ["0.7 w"]
    width = table.cell_w * len(table.columns)
    height = table.cell_h * (len(table.rows) + 1)
    for row in range(len(table.rows) + 1):
        y = table.y + row * table.cell_h
        parts.append(f"{table.x} {y} m {table.x + width} {y} l S")
    for column in range(len(table.columns) + 1):
        x = table.x + column * table.cell_w
        parts.append(f"{x} {table.y} m {x} {table.y + height} l S")
    grid = (table.columns, *table.rows)
    for row_index, row in enumerate(grid):
        baseline = table.y + height - (row_index + 1) * table.cell_h + 6
        for column_index, cell in enumerate(row):
            x = table.x + column_index * table.cell_w + 4
            parts.append(f"BT /F1 9 Tf {x} {baseline} Td ({_escape(cell)}) Tj ET")
    return parts


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_page(lines: list[str]) -> PageSpec:
    return PageSpec(
        lines=[(72.0, 720 - index * 18, 10, line) for index, line in enumerate(lines)]
    )


def _single_table() -> tuple[list[PageSpec], dict[str, Any]]:
    table = TableSpec(
        x=72,
        y=560,
        columns=("Item", "Qty", "Amount"),
        rows=(("Widget", "2", "20.00"), ("Gadget", "1", "15.50"), ("Bolt", "10", "5.00")),
    )
    page = PageSpec(
        lines=[(72.0, 720.0, 10, "Purchase order summary")],
        table=table,
    )
    truth = {
        "pages": [
            {
                "page_no": 1,
                "text_lines": ["Purchase order summary"],
                "table": {"header": list(table.columns), "rows": [list(r) for r in table.rows]},
            }
        ],
        "reading_order": ["Purchase order summary"],
    }
    return [page], truth


def _cross_page_table() -> tuple[list[PageSpec], dict[str, Any]]:
    header = ("Item", "Qty", "Amount")
    page1_rows = (("Widget", "2", "20.00"), ("Gadget", "1", "15.50"), ("Bolt", "10", "5.00"))
    page2_rows = (("Nut", "4", "8.00"), ("Washer", "6", "3.60"))
    pages = [
        PageSpec(
            lines=[(72.0, 720.0, 10, "Purchase order summary")],
            table=TableSpec(x=72, y=560, columns=header, rows=page1_rows),
        ),
        PageSpec(
            lines=[(72.0, 720.0, 10, "Purchase order summary continued")],
            table=TableSpec(x=72, y=640, columns=header, rows=page2_rows),
        ),
    ]
    truth = {
        "pages": [
            {"page_no": 1, "text_lines": ["Purchase order summary"], "table": None},
            {"page_no": 2, "text_lines": ["Purchase order summary continued"], "table": None},
        ],
        "tables": [
            {
                "header": list(header),
                "rows": [list(row) for row in (*page1_rows, *page2_rows)],
            }
        ],
        "reading_order": ["Purchase order summary", "Purchase order summary continued"],
    }
    return pages, truth


def _multi_column() -> tuple[list[PageSpec], dict[str, Any]]:
    # 参差行宽（右边缘不齐）才会被识别为分栏而不是表格
    left: list[str] = []
    right: list[str] = []
    words_left = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta"]
    words_right = ["one", "two", "three", "four", "five", "six", "seven"]
    for index in range(1, 21):
        left.append(
            f"L{index} " + " ".join(words_left[: (index % 5) + 4])
        )
        right.append(
            f"R{index} " + " ".join(words_right[: (index * 3 % 5) + 4])
        )
    page = PageSpec(
        lines=[
            *[(72.0, 730 - index * 16, 10, line) for index, line in enumerate(left)],
            *[(330.0, 726 - index * 16, 10, line) for index, line in enumerate(right)],
        ]
    )
    truth = {
        "pages": [{"page_no": 1, "text_lines": [*left, *right], "table": None}],
        "reading_order": [*left, *right],
    }
    return [page], truth


def _text_only(index: int) -> tuple[list[PageSpec], dict[str, Any]]:
    lines = [f"Plain text page {index} line {line}" for line in range(1, 7)]
    truth = {
        "pages": [{"page_no": 1, "text_lines": lines, "table": None}],
        "reading_order": lines,
    }
    return [_text_page(lines)], truth


def generate(root: Path = GOLDEN_ROOT) -> list[dict[str, Any]]:
    output = root / "constructed"
    output.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    builders = {
        "text_only": lambda index: _text_only(index),
        "single_table": lambda index: _single_table(),
        "cross_page_table": lambda index: _cross_page_table(),
        "multi_column": lambda index: _multi_column(),
    }
    counter = 0
    for category, builder in builders.items():
        for index in range(1, 31):
            pages, truth = builder(index)
            sample_id = f"C-{counter:04d}"
            counter += 1
            pdf_path = output / f"{sample_id}.pdf"
            truth_path = output / f"{sample_id}.truth.json"
            pdf_path.write_bytes(build_pdf(pages))
            truth.update(
                {
                    "sample_id": sample_id,
                    "category": category,
                    "source": "constructed",
                    "pdf_path": str(pdf_path),
                }
            )
            write_json(truth_path, truth)
            samples.append(
                {
                    "sample_id": sample_id,
                    "category": category,
                    "source": "constructed",
                    "doc_id": "",
                    "file": str(pdf_path),
                    "page_no": 1,
                    "size_bytes": pdf_path.stat().st_size,
                    "has_table": str(category in {"single_table", "cross_page_table"}).lower(),
                    "has_column": str(category == "multi_column").lower(),
                    "needs_ocr": "false",
                    "encoding_bad": "false",
                    "truth_path": str(truth_path),
                }
            )
    return samples


if __name__ == "__main__":
    created = generate()
    print(json.dumps({"constructed": len(created)}, ensure_ascii=False))
