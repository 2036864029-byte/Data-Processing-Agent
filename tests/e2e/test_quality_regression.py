"""黄金样本质量断言（Phase 6 回归面）。

语料不入库；本地缺失时整文件 skip。断言只锁“用户可见的缺陷是否消失/保留”，
不锁具体文本内容；表格形状是唯一的金值锚点（行合并/结构回归点）。

覆盖：
- 4 个假缺口页转 pass（R-0002 / R-0077 / R-0098 / R-0102）；
- 2 个扫描页仍为缺口且 `needs_ocr=True`（R-0139 / R-0140）；
- 2 个伪表页交付 0 张表（R-0033 / R-0036）；
- R-0023 的编号与数字错序修复；
- R-0041 表格结构不回退。
"""

from __future__ import annotations

from pathlib import Path

import pdf_inspector
import pytest

from dataagent.contracts.messages import Verdict
from dataagent.contracts.plugin import PluginContext
from dataagent.contracts.policies import PdfType
from dataagent.pipeline.merge.merger import MergedDocument, MergedPage, ValidationResult
from dataagent.pipeline.output.structure_view import build_structure, to_markdown_view
from dataagent.pipeline.quality.l1_check import L1_CHECKER
from dataagent.pipeline.quality.l1_input import build_l1_input
from dataagent.pipeline.quality.normalization import normalize_text
from dataagent.pipeline.structure.build import build_document_blocks
from dataagent.pipeline.structure.models import PageLayout
from dataagent.pipeline.table.geometry import PositionedText
from dataagent.platform.plugins.adapters.engine_pdf import PLUGIN
from dataagent.platform.plugins.adapters.layout_probe import probe_layout
from dataagent.platform.plugins.adapters.table_pdfplumber import PdfplumberTableSource

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "doc" / "test_pdf"
DOC_ID = "a" * 64
TEXT_AREA_CM2 = 468.0

FILES = {
    "tender_law": CORPUS / "中华人民共和国招标投标法_中国政府采购网.pdf",
    "q1200": CORPUS / "政府采购、工程招标、投标与评标1200问（第3版）_刘海桑 编著.pdf",
    "central": CORPUS / "财政部关于印发《中央单位政府采购管理实施办法》的通知_中国政府采购网.pdf",
    "multi": CORPUS / "2607200001.pdf",
    "maotai": CORPUS / "贵州茅台2024年年度报告全文.pdf",
    "guide": CORPUS / "招标投标法律解读与风险防范实务 (白如银) (Z-Library).pdf",
}

pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in FILES.values()),
    reason="真实语料不在本地",
)


def extract_page(path: Path, page: int) -> tuple[dict[str, object], str]:
    raw = PLUGIN.invoke(
        "pdf.text.extract",
        {"path": str(path), "pages": [page]},
        PluginContext("t", DOC_ID),
    )
    artifact = raw["pages"][0]
    return raw, normalize_text(str(artifact["markdown"]))


def l1_report(path: Path, page: int):
    raw, markdown = extract_page(path, page)
    pdf_type = PdfType(pdf_inspector.detect_pdf(str(path)).pdf_type)
    return raw["pages"][0], L1_CHECKER.check(
        build_l1_input(
            doc_id=DOC_ID,
            page_no=page,
            markdown=markdown,
            pdf_type=pdf_type,
            declared_table=page in raw["pages_with_tables"],
            has_columns=page in raw["pages_with_columns"],
            text_area=TEXT_AREA_CM2,
        )
    )


def page_layout(path: Path, page: int) -> PageLayout:
    probed = probe_layout(str(path), item_pages=[page], role_pages=[page])
    return PageLayout(
        page_no=page,
        items=tuple(
            PositionedText(
                text=value.text,
                x=value.x,
                y=value.y,
                width=value.width,
                height=value.height,
                mcid=value.mcid,
            )
            for value in probed.items
            if value.page == page
        ),
        roles=tuple(
            (value.mcid, value.role)
            for value in probed.roles
            if value.page == page
        ),
    )


def build_page(path: Path, page: int, markdown: str):
    with PdfplumberTableSource(str(path)) as source:
        ruled = source.extract_page(page)
    document = MergedDocument(
        doc_id=DOC_ID,
        pages=(MergedPage(page_no=page, markdown=markdown),),
        validation=ValidationResult(),
    )
    return build_structure(
        build_document_blocks(
            document,
            layouts={page: page_layout(path, page)},
            ruled_tables=lambda _page: ruled,
        ),
        doc_id=DOC_ID,
    )


@pytest.mark.parametrize(
    ("key", "page", "sample"),
    [
        ("tender_law", 2, "R-0002"),
        ("multi", 12, "R-0077"),
        ("q1200", 365, "R-0098"),
        ("q1200", 436, "R-0102"),
    ],
)
def test_false_gap_pages_now_pass(key: str, page: int, sample: str) -> None:
    _artifact, report = l1_report(FILES[key], page)

    assert report.verdict is Verdict.PASS, (
        sample,
        [defect.code.value for defect in report.defects],
    )


@pytest.mark.parametrize(
    ("key", "page", "sample"),
    [("guide", 1, "R-0139"), ("q1200", 39, "R-0140")],
)
def test_scan_pages_remain_gaps_for_ocr(key: str, page: int, sample: str) -> None:
    artifact, report = l1_report(FILES[key], page)

    assert artifact["needs_ocr"] is True, sample
    assert report.verdict is Verdict.FAIL, sample


@pytest.mark.parametrize(
    ("key", "page", "sample"),
    [("central", 5, "R-0033"), ("q1200", 109, "R-0036")],
)
def test_prose_pages_deliver_no_table(key: str, page: int, sample: str) -> None:
    _raw, markdown = extract_page(FILES[key], page)

    structure = build_page(FILES[key], page, markdown)

    tables = [
        block
        for page_blocks in structure.pages
        for block in page_blocks.blocks
        if getattr(block, "type", None) == "table"
    ]
    assert tables == [], sample


def test_r0023_numbering_and_word_order() -> None:
    _raw, markdown = extract_page(FILES["q1200"], 104)

    structure = build_page(FILES["q1200"], 104, markdown)
    rendered = to_markdown_view(
        structure, page_order=[104], fallback={104: markdown}
    )

    assert "答：（1）" in rendered
    assert "1 答：" not in rendered
    assert "造成21名" in rendered
    assert "8人受伤" in rendered


def test_r0041_table_structure_is_stable() -> None:
    """茅台 p5 三张表的行列数是 rowmerge 回归锚点（不是文本快照）。"""
    _raw, markdown = extract_page(FILES["maotai"], 5)

    structure = build_page(FILES["maotai"], 5, markdown)

    shapes = sorted(
        (block.grid.n_rows, block.grid.n_cols)
        for page_blocks in structure.pages
        for block in page_blocks.blocks
        if getattr(block, "type", None) == "table"
    )
    assert shapes == [(3, 3), (6, 5), (9, 5)]
