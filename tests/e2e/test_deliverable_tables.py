"""四页真实样本 e2e：交付表格与 route_tables 选中结果一致（PR-1 验收）。

语料不入库；本地缺失时 skip。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pdf_inspector
import pytest

from dataagent.pipeline.merge.merger import (
    MergedDocument,
    MergedPage,
    ValidationResult,
)
from dataagent.pipeline.structure.build import build_document_blocks, build_page_blocks
from dataagent.pipeline.structure.models import PageLayout
from dataagent.pipeline.structure.render import render_document_markdown
from dataagent.pipeline.table.geometry import PositionedText
from dataagent.pipeline.table.router import route_tables
from dataagent.pipeline.table.rowmerge import merge_logical_rows
from dataagent.platform.plugins.adapters.layout_probe import probe_layout
from dataagent.platform.plugins.adapters.table_pdfplumber import PdfplumberTableSource

ROOT = Path(__file__).resolve().parents[2]
PDF = ROOT / "doc" / "test_pdf" / "贵州茅台2024年年度报告全文.pdf"
PAGES = (24, 25, 60, 86)

pytestmark = pytest.mark.skipif(not PDF.exists(), reason="真实语料不在本地")


def page_inputs(page: int) -> tuple[str, PageLayout, tuple]:
    markdown = (
        pdf_inspector.extract_pages_markdown(str(PDF), pages=[page - 1])
        .pages[0]
        .markdown
        or ""
    )
    probed = probe_layout(str(PDF), item_pages=[page], role_pages=[page])
    layout = PageLayout(
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
        ),
        roles=tuple((value.mcid, value.role) for value in probed.roles),
    )
    with PdfplumberTableSource(str(PDF)) as source:
        ruled = source.extract_page(page)
    return markdown, layout, ruled


def signature(grid) -> tuple:
    return tuple(
        sorted(
            (cell.row, cell.col, cell.text, cell.rowspan, cell.colspan)
            for cell in grid.cells
        )
    )


@pytest.mark.parametrize("page", PAGES)
def test_selected_tables_match_router(page: int) -> None:
    markdown, layout, ruled = page_inputs(page)
    routing = route_tables(
        markdown, layout.items, page_no=page, ruled_tables=ruled
    )
    result = build_page_blocks(
        page_no=page,
        markdown=markdown,
        layout=layout,
        ruled_tables=ruled,
    )

    expected = sorted(
        signature(merge_logical_rows(choice.grid))
        for choice in routing.tables
    )
    actual = sorted(
        signature(block.table.grid)
        for block in result.page.blocks
        if block.kind == "table" and block.table is not None
    )
    assert actual == expected

    trusted = [
        block.bbox
        for block in result.page.blocks
        if block.kind == "table"
        and block.table is not None
        and block.table.trusted_bbox
    ]
    for block in result.page.blocks:
        if block.kind != "text":
            continue
        center_x = (block.bbox[0] + block.bbox[2]) / 2
        center_y = (block.bbox[1] + block.bbox[3]) / 2
        assert not any(
            box[0] <= center_x <= box[2] and box[1] <= center_y <= box[3]
            for box in trusted
        )


def test_p25_personnel_table_is_logically_merged() -> None:
    markdown, layout, ruled = page_inputs(25)

    result = build_page_blocks(
        page_no=25, markdown=markdown, layout=layout, ruled_tables=ruled
    )

    tables = [
        block.table.grid
        for block in result.page.blocks
        if block.kind == "table" and block.table is not None
    ]
    assert len(tables) == 1
    grid = tables[0]
    assert (grid.n_rows, grid.n_cols) == (14, 12)
    names = [cell.text for cell in grid.cells if cell.col == 0 and cell.row > 0]
    assert names == [
        "张德芹",
        "王 莉",
        "郭田勇",
        "盛雷鸣",
        "王 鑫",
        "刘世仲",
        "韦 芳",
        "游亚林",
        "郑尚勋",
        "闻 勇",
        "钟正强",
        "蒋 焰",
        "向 平",
    ]
    merged = {(cell.row, cell.col): cell.text for cell in grid.cells}
    assert merged[(1, 1)] == "党委书记\n董事长、董事"
    assert merged[(1, 4)] == "2024年04月30日\n2024年05月29日"


def test_document_renders_each_table_id_once() -> None:
    inputs = {page: page_inputs(page) for page in PAGES}
    document = MergedDocument(
        doc_id="doc",
        pages=tuple(
            MergedPage(page_no=page, markdown=inputs[page][0]) for page in PAGES
        ),
        validation=ValidationResult(),
    )
    layouts = {page: inputs[page][1] for page in PAGES}

    blocks = build_document_blocks(
        document,
        layouts=layouts,
        ruled_tables=lambda page: inputs[page][2],
    )
    rendered = render_document_markdown(blocks, fallback={})

    counts = Counter(
        line for line in rendered.splitlines() if line.startswith("<!--table:")
    )
    assert counts
    assert all(count == 1 for count in counts.values())
    expected = sum(
        1
        for page in blocks.pages
        for block in page.blocks
        if block.kind == "table"
    )
    assert sum(counts.values()) == expected
