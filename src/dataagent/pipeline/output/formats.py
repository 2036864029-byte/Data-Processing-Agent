"""输出格式（B7/T114 / FR-018）：JSON / CSV / Markdown / Excel。

缺口页标记内嵌在每种格式里（Markdown 可识别注释块、JSON `gap: true`、
CSV/Excel `gap` 列），下游拿到产物即可看到缺口。
"""

from __future__ import annotations

import csv
import io
import json

from openpyxl import Workbook

from dataagent.pipeline.merge.merger import MergedDocument, MergedPage
from dataagent.pipeline.output.delivery import DeliveryDecision

COLUMNS: tuple[str, ...] = (
    "page_no",
    "gap",
    "extract_quality",
    "defect_codes",
    "markdown",
)


def to_json(document: MergedDocument, decision: DeliveryDecision) -> str:
    payload = {
        "doc_id": document.doc_id,
        "delivery_level": decision.level.value,
        "gap_ratio": round(decision.gap_ratio, 6),
        "requires_manual_review": decision.requires_manual_review,
        "gap_pages": list(document.gap_pages),
        "validation": {
            "ok": document.validation.ok,
            "findings": [
                {
                    "code": finding.code,
                    "message": finding.message,
                    "page_no": finding.page_no,
                }
                for finding in document.validation.findings
            ],
        },
        "pages": [
            {
                "page_no": page.page_no,
                "gap": page.gap,
                "extract_quality": page.extract_quality,
                "defect_codes": [code.value for code in page.defect_codes],
                "markdown": page.markdown,
            }
            for page in document.pages
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def to_markdown(document: MergedDocument, decision: DeliveryDecision) -> str:
    return document.markdown


def to_csv(document: MergedDocument, decision: DeliveryDecision) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for page in document.pages:
        writer.writerow(_row(page))
    return buffer.getvalue()


def to_excel(document: MergedDocument, decision: DeliveryDecision) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "pages"
    sheet.append(list(COLUMNS))
    for page in document.pages:
        sheet.append(list(_row(page)))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _row(page: MergedPage) -> tuple[object, ...]:
    return (
        page.page_no,
        "true" if page.gap else "false",
        "" if page.extract_quality is None else page.extract_quality,
        ",".join(code.value for code in page.defect_codes),
        page.markdown,
    )
