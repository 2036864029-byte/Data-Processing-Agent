"""结构视图测试：rows_text、块/表 ID、{meta, structure}、md/json 同源。"""

from __future__ import annotations

import json

from dataagent.contracts.structure import TableBlock
from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.merge.merger import (
    MergedDocument,
    MergedPage,
    ValidationResult,
)
from dataagent.pipeline.output.delivery import decide_delivery
from dataagent.pipeline.output.structure_view import (
    build_meta,
    build_structure,
    rows_text,
    to_json_view,
    to_markdown_view,
    to_tables_json,
)
from dataagent.pipeline.structure.build import build_document_blocks
from dataagent.pipeline.structure.render import render_document_markdown


def grid(
    cells: tuple[TableCell, ...],
    *,
    n_rows: int = 2,
    n_cols: int = 2,
    header_rows: int = 1,
) -> TableGrid:
    return TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 100.0, 50.0),
        n_rows=n_rows,
        n_cols=n_cols,
        header_rows=header_rows,
        cells=cells,
        source_engine="test",
        confidence=0.9,
    )


def document() -> MergedDocument:
    table = "| 项目 | 金额 |\n| --- | --- |\n| 合计 | 100 |\n"
    return MergedDocument(
        doc_id="doc",
        pages=(
            MergedPage(page_no=1, markdown="# 标题\n\n正文\n\n" + table),
            MergedPage(page_no=2, markdown="<!-- gap -->\n", gap=True),
            MergedPage(page_no=3, markdown="第三页\n"),
        ),
        validation=ValidationResult(),
    )


def test_rows_text_uses_header_names() -> None:
    value = grid(
        (
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额"),
            TableCell(row=1, col=0, text="合计"),
            TableCell(row=1, col=1, text="100"),
        )
    )

    assert rows_text(value) == "项目: 合计；金额: 100"


def test_rows_text_repeats_merged_values() -> None:
    value = grid(
        (
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额"),
            TableCell(row=0, col=2, text="占比"),
            TableCell(row=1, col=0, text="A", rowspan=2),
            TableCell(row=1, col=1, text="10"),
            TableCell(row=1, col=2, text="10%"),
            TableCell(row=2, col=1, text="20"),
            TableCell(row=2, col=2, text="20%"),
        ),
        n_rows=3,
        n_cols=3,
    )

    assert rows_text(value) == "项目: A；金额: 10；占比: 10%\n项目: A；金额: 20；占比: 20%"


def test_rows_text_multi_row_header_and_empty_cells() -> None:
    value = grid(
        (
            TableCell(row=0, col=0, text="项目", rowspan=2),
            TableCell(row=0, col=1, text="金额"),
            TableCell(row=1, col=1, text="本期"),
            TableCell(row=2, col=0, text="A"),
            TableCell(row=2, col=1, text="1"),
        ),
        n_rows=3,
        n_cols=2,
        header_rows=2,
    )

    assert rows_text(value) == "项目: A；金额/本期: 1"


def test_rows_text_empty_when_no_data_rows() -> None:
    value = grid(
        (
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额"),
        ),
        n_rows=1,
        n_cols=2,
    )

    assert rows_text(value) == ""


def test_rows_text_collapses_cell_newlines() -> None:
    value = grid(
        (
            TableCell(row=0, col=0, text="姓名"),
            TableCell(row=0, col=1, text="职务"),
            TableCell(row=1, col=0, text="张德芹"),
            TableCell(row=1, col=1, text="党委书记\n董事长、董事"),
        )
    )

    text = rows_text(value)

    assert text == "姓名: 张德芹；职务: 党委书记 董事长、董事"
    assert "\n" not in text


def test_build_structure_assigns_ids_and_skips_gaps() -> None:
    source = document()
    blocks = build_document_blocks(source, layouts={})

    structure = build_structure(blocks, doc_id="doc")

    assert [page.page_no for page in structure.pages] == [1, 3]
    first = structure.pages[0].blocks
    assert [block.block_id for block in first] == ["b0001", "b0002", "b0003"]
    assert first[0].level == 1
    assert first[0].role == "heading"
    table = next(block for block in first if isinstance(block, TableBlock))
    assert table.table_id == "t001"
    assert table.rows_text == "项目: 合计；金额: 100"
    assert structure.pages[1].blocks[0].block_id == "b0004"


def test_build_structure_smooths_heading_levels() -> None:
    source = MergedDocument(
        doc_id="doc",
        pages=(
            MergedPage(page_no=1, markdown="# 一级\n\n### 三级\n\n##### 五级\n"),
        ),
        validation=ValidationResult(),
    )
    blocks = build_document_blocks(source, layouts={})

    structure = build_structure(blocks, doc_id="doc")

    levels = [
        block.level
        for block in structure.pages[0].blocks
        if block.type == "text" and block.level is not None
    ]
    assert levels == [1, 2, 3]
    rendered = to_markdown_view(
        structure, page_order=[1], fallback={1: source.pages[0].markdown}
    )
    assert "# 一级" in rendered
    assert "## 三级" in rendered
    assert "### 五级" in rendered
    assert "### 三级" not in rendered
    assert "##### 五级" not in rendered


def test_build_meta_contains_delivery_and_gaps() -> None:
    source = document()
    meta = build_meta(source, decide_delivery(source))

    assert meta["doc_id"] == "doc"
    assert set(meta) == {
        "doc_id",
        "delivery_level",
        "gap_ratio",
        "requires_manual_review",
        "validation",
        "gaps",
    }
    assert meta["gaps"] == [{"page_no": 2, "defect_codes": []}]


def test_json_view_has_meta_and_structure() -> None:
    source = document()
    blocks = build_document_blocks(source, layouts={})
    structure = build_structure(blocks, doc_id="doc")
    meta = build_meta(source, decide_delivery(source))

    payload = json.loads(to_json_view(structure, meta))

    assert set(payload) == {"meta", "structure"}
    assert payload["structure"]["doc_id"] == "doc"
    pages = payload["structure"]["pages"]
    assert [page["page_no"] for page in pages] == [1, 3]
    table = next(
        block
        for block in pages[0]["blocks"]
        if block["type"] == "table"
    )
    assert table["table_id"] == "t001"
    assert table["rows_text"] == "项目: 合计；金额: 100"
    assert table["grid"]["n_rows"] == 2
    assert table["grid"]["page_span"] == [1, 1]


def test_tables_json_contains_only_table_blocks() -> None:
    source = document()
    blocks = build_document_blocks(source, layouts={})
    structure = build_structure(blocks, doc_id="doc")

    payload = json.loads(to_tables_json(structure, doc_id="doc"))

    assert payload["doc_id"] == "doc"
    assert len(payload["tables"]) == 1
    table = payload["tables"][0]
    assert table["table_id"] == "t001"
    assert table["page_no"] == 1
    assert table["page_span"] == [1, 1]
    assert table["rows_text"] == "项目: 合计；金额: 100"
    assert table["grid"]["n_rows"] == 2
    assert "structure" not in payload


def test_tables_json_handles_missing_structure() -> None:
    payload = json.loads(to_tables_json(None, doc_id="doc"))

    assert payload == {"doc_id": "doc", "tables": []}


def test_markdown_view_matches_internal_renderer_and_keeps_gap() -> None:
    source = document()
    blocks = build_document_blocks(source, layouts={})
    structure = build_structure(blocks, doc_id="doc")
    fallback = {page.page_no: page.markdown for page in source.pages}

    rendered = to_markdown_view(
        structure, page_order=[1, 2, 3], fallback=fallback
    )

    assert rendered == render_document_markdown(blocks, fallback=fallback)
    assert "<!--table:t001-->" in rendered
    assert "gap" in rendered


def test_md_and_json_table_ids_reconcile() -> None:
    source = document()
    blocks = build_document_blocks(source, layouts={})
    structure = build_structure(blocks, doc_id="doc")
    meta = build_meta(source, decide_delivery(source))
    fallback = {page.page_no: page.markdown for page in source.pages}

    markdown = to_markdown_view(
        structure, page_order=[1, 2, 3], fallback=fallback
    )
    payload = json.loads(to_json_view(structure, meta))

    md_ids = {
        line.split(":", 1)[1].rstrip("->")
        for line in markdown.splitlines()
        if line.startswith("<!--table:")
    }
    json_ids = {
        block["table_id"]
        for page in payload["structure"]["pages"]
        for block in page["blocks"]
        if block["type"] == "table"
    }
    assert md_ids == json_ids
