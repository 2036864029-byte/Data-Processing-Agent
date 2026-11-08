"""四页真实样本 e2e：交付表格与 route_tables 选中结果一致（PR-1 验收）。

语料不入库；本地缺失时 skip。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pdf_inspector
import pytest

from dataagent.pipeline.merge.merger import (
    MergedDocument,
    MergedPage,
    ValidationResult,
)
from dataagent.pipeline.quality.text_normalize import (
    normalize_cell_text,
    normalize_table_grid,
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
        signature(merge_logical_rows(normalize_table_grid(choice.grid)))
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


@pytest.mark.parametrize("page", PAGES)
def test_table_cells_are_normalized(page: int) -> None:
    """回归锚点：同一份年报的 4 个表页（不同表格来源/形状）逐行已是归一形态。

    这提供真实流水线上的回归面，**不是泛化**——4 页都来自同一文档同一夹具族。
    输入域上的保证由 `tests/unit/pipeline/quality/test_cell_normalize_properties.py`
    的性质测试承担。
    """
    markdown, layout, ruled = page_inputs(page)

    result = build_page_blocks(
        page_no=page, markdown=markdown, layout=layout, ruled_tables=ruled
    )

    grids = [
        block.table.grid
        for block in result.page.blocks
        if block.kind == "table" and block.table is not None
    ]
    assert grids
    for grid in grids:
        for cell in grid.cells:
            assert "\r" not in cell.text
            for line in cell.text.split("\n"):
                assert line == normalize_cell_text(line)


_PERSON_RE = re.compile(r"^[一-鿿·]{2,6}$")
_DATE_RE = re.compile(r"^\d{4}年\d{2}月\d{2}日$")


def test_p25_personnel_table_is_logically_merged() -> None:
    """p25 人员表：物理行合并为逻辑行，列内多值以语义换行保留。

    断言不绑定具体人名——只验证「每逻辑行一个称谓、无重复、无伪空格、
    多值单元格的每行非空、日期按行承载」这些行合并应有的性质。
    """
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
    assert len(names) == grid.n_rows - grid.header_rows
    assert len(set(names)) == len(names)
    assert all(_PERSON_RE.match(name) for name in names)

    multi_value = [cell.text for cell in grid.cells if "\n" in cell.text]
    assert multi_value
    assert all(
        all(part.strip() for part in text.split("\n")) for text in multi_value
    )
    date_rows = [
        parts
        for parts in (text.split("\n") for text in multi_value)
        if all(_DATE_RE.match(part) for part in parts)
    ]
    assert date_rows, "应存在按行承载的日期列（纵向合并证据）"


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
