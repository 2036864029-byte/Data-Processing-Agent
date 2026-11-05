"""PR-1 建块测试：markdown 回退、行聚类、表格槽位、安全阀、caption、ID。"""

from __future__ import annotations

import dataagent.pipeline.structure.build as build_module
from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.merge.merger import (
    MergedDocument,
    MergedPage,
    ValidationResult,
    merge_document,
)
from dataagent.pipeline.structure.build import build_document_blocks, build_page_blocks
from dataagent.pipeline.structure.models import PageLayout
from dataagent.pipeline.table.geometry import PositionedText
from dataagent.pipeline.table.quality import grid_quality
from dataagent.pipeline.table.router import TableChoice, TableRouting


def item(
    text: str,
    x: float,
    y: float,
    *,
    width: float = 40.0,
    height: float = 10.0,
    mcid: int | None = None,
) -> PositionedText:
    return PositionedText(
        text=text, x=x, y=y, width=width, height=height, mcid=mcid
    )


def layout(items: list[PositionedText], *, page_no: int = 1) -> PageLayout:
    return PageLayout(page_no=page_no, items=tuple(items))


def merged(page_markdowns: list[str]) -> MergedDocument:
    return MergedDocument(
        doc_id="doc",
        pages=tuple(
            MergedPage(page_no=index + 1, markdown=markdown)
            for index, markdown in enumerate(page_markdowns)
        ),
        validation=ValidationResult(),
    )


def table_markdown() -> str:
    return "| 项目 | 金额 |\n| --- | --- |\n| 合计 | 100 |\n"


def table_items() -> list[PositionedText]:
    return [
        item("项目", 50, 700),
        item("金额", 200, 700),
        item("合计", 50, 680),
        item("100", 200, 680),
    ]


def test_markdown_path_builds_heading_paragraph_and_table() -> None:
    result = build_page_blocks(
        page_no=1,
        markdown="# 标题\n\n正文第一行\n正文第二行\n\n" + table_markdown(),
        layout=PageLayout(page_no=1),
    )

    kinds = [block.kind for block in result.page.blocks]
    assert kinds == ["heading", "text", "table"]
    heading, paragraph, table = result.page.blocks
    assert heading.level == 1
    assert heading.text == "标题"
    assert paragraph.text == "正文第一行\n正文第二行"
    assert table.table is not None
    assert (table.table.grid.n_rows, table.table.grid.n_cols) == (2, 2)
    assert result.warnings == ()


def test_item_path_clusters_lines_and_merges_paragraphs() -> None:
    items = [
        item("二、章节", 50, 700, width=80),
        item("第一段第一行", 50, 680, width=80),
        item("继续内容", 50, 664, width=60),
        item("结束。", 50, 648, width=34),
    ]

    result = build_page_blocks(
        page_no=1,
        markdown="## 二、章节\n\n第一段第一行继续内容结束。\n",
        layout=layout(items),
    )

    heading = result.page.blocks[0]
    paragraph = result.page.blocks[1]
    assert heading.kind == "heading"
    assert heading.level == 2
    assert heading.text == "二、章节"
    assert paragraph.kind == "text"
    assert paragraph.text == "第一段第一行继续内容结束。"


def test_heading_level_comes_from_markdown_not_structure_role() -> None:
    items = [item("二、章节", 50, 700, width=80, mcid=9)]

    result = build_page_blocks(
        page_no=1,
        markdown="## 二、章节\n",
        layout=PageLayout(page_no=1, items=tuple(items), roles=((9, "H1"),)),
    )

    assert result.page.blocks[0].level == 2


def test_table_slot_replaces_l0_and_excludes_table_items() -> None:
    items = [*table_items(), item("正文", 50, 600, width=60)]

    result = build_page_blocks(
        page_no=1,
        markdown=table_markdown() + "\n正文\n",
        layout=layout(items),
    )

    tables = [block for block in result.page.blocks if block.kind == "table"]
    texts = [block for block in result.page.blocks if block.kind == "text"]
    assert len(tables) == 1
    assert [block.text for block in texts] == ["正文"]
    assert all("合计" not in block.text for block in texts)
    assert tables[0].table is not None
    assert tables[0].table.trusted_bbox is True


def test_untrusted_bbox_does_not_swallow_and_warns() -> None:
    items = [item("正文", 50, 600, width=60)]

    result = build_page_blocks(
        page_no=1,
        markdown=table_markdown() + "\n正文\n",
        layout=layout(items),
    )

    tables = [block for block in result.page.blocks if block.kind == "table"]
    texts = [block for block in result.page.blocks if block.kind == "text"]
    assert len(tables) == 1
    assert tables[0].table is not None
    assert tables[0].table.trusted_bbox is False
    assert any("正文" in block.text for block in texts)
    assert "table_bbox_untrusted:1" in result.warnings


def test_caption_is_extracted_and_consumed() -> None:
    result = build_page_blocks(
        page_no=1,
        markdown="表 3-1 合并资产负债表\n\n" + table_markdown(),
        layout=PageLayout(page_no=1),
    )

    tables = [block for block in result.page.blocks if block.kind == "table"]
    assert len(tables) == 1
    assert tables[0].table is not None
    assert tables[0].table.caption == "表 3-1 合并资产负债表"
    assert all(
        "合并资产负债表" not in block.text
        for block in result.page.blocks
        if block.kind == "text"
    )


def test_no_items_page_does_not_warn_about_bbox() -> None:
    result = build_page_blocks(
        page_no=1,
        markdown=table_markdown(),
        layout=PageLayout(page_no=1),
    )

    assert [block.kind for block in result.page.blocks] == ["table"]
    assert result.warnings == ()


def test_l0_missed_table_is_inserted_from_geometry() -> None:
    items = [
        item("A", 50, 720),
        item("B", 200, 720),
        item("1", 50, 704),
        item("2", 200, 704),
        item("3", 50, 688),
        item("4", 200, 688),
        item("正文第一行", 50, 600, width=80),
        item("正文第二行", 50, 584, width=80),
    ]

    result = build_page_blocks(
        page_no=1,
        markdown="正文第一行正文第二行\n",
        layout=layout(items),
    )

    tables = [block for block in result.page.blocks if block.kind == "table"]
    texts = [block for block in result.page.blocks if block.kind == "text"]
    assert len(tables) == 1
    assert tables[0].table is not None
    assert tables[0].table.source == "geometry"
    assert "".join(block.text for block in texts) == "正文第一行正文第二行"


def test_document_assigns_unique_table_ids_in_reading_order() -> None:
    document = merged(
        [table_markdown(), "前言\n\n" + table_markdown()]
    )

    blocks = build_document_blocks(
        document,
        layouts={1: PageLayout(page_no=1), 2: PageLayout(page_no=2)},
    )

    table_ids = [
        block.table.table_id
        for page in blocks.pages
        for block in page.blocks
        if block.kind == "table" and block.table is not None
    ]
    assert table_ids == ["t001", "t002"]


def test_gap_page_has_no_blocks() -> None:
    document = MergedDocument(
        doc_id="doc",
        pages=(
            MergedPage(page_no=1, markdown="<!-- gap -->\n", gap=True),
            MergedPage(page_no=2, markdown="正文\n"),
        ),
        validation=ValidationResult(),
    )

    blocks = build_document_blocks(
        document, layouts={2: PageLayout(page_no=2)}
    )

    assert blocks.pages[0].blocks == ()
    assert blocks.pages[0].gap is True
    assert [block.kind for block in blocks.pages[1].blocks] == ["text"]


def test_build_is_deterministic() -> None:
    first = build_page_blocks(
        page_no=1, markdown=table_markdown(), layout=PageLayout(page_no=1)
    )
    second = build_page_blocks(
        page_no=1, markdown=table_markdown(), layout=PageLayout(page_no=1)
    )

    assert first == second


def test_cross_page_merged_table_becomes_single_block() -> None:
    artifacts = (
        PageArtifact(
            doc_id="doc",
            page_no=1,
            engine="test",
            params_hash="h",
            markdown="| a | b |\n|---|---|\n| 1 | 2 |\n",
        ),
        PageArtifact(
            doc_id="doc",
            page_no=2,
            engine="test",
            params_hash="h",
            markdown="| a | b |\n|---|---|\n| 3 | 4 |\n",
        ),
    )

    document = merge_document("doc", artifacts, ())
    blocks = build_document_blocks(document, layouts={})

    tables = [
        block
        for page in blocks.pages
        for block in page.blocks
        if block.kind == "table"
    ]
    assert len(tables) == 1
    assert tables[0].table is not None
    assert tables[0].table.grid.n_rows == 3
    assert tables[0].table.grid.page_span == (1, 2)


def prose(length: int) -> str:
    return ("决议均得到认真执行。全体股东的合法权益。公司规范运作。" * 20)[:length]


def test_prose_like_l0_table_is_demoted_to_text() -> None:
    long_cell = prose(180)
    markdown = (
        f"|{long_cell}|{long_cell}|{long_cell}|\n"
        "|---|---|---|\n"
        "|会议届次|召开日期|决议刊登的指定网|\n"
    )

    result = build_page_blocks(
        page_no=1, markdown=markdown, layout=PageLayout(page_no=1)
    )

    assert [block.kind for block in result.page.blocks] == ["text"]
    text = result.page.blocks[0].text
    assert "决议均得到认真执行" in text
    assert "会议届次" in text
    assert "|" not in text
    assert "table_l0_prose_demoted:1" in result.warnings


def test_normal_l0_table_is_not_demoted() -> None:
    result = build_page_blocks(
        page_no=1, markdown=table_markdown(), layout=PageLayout(page_no=1)
    )

    assert [block.kind for block in result.page.blocks] == ["table"]
    assert result.warnings == ()


def test_long_cell_without_sentence_punctuation_is_not_demoted() -> None:
    long_cell = "很长的说明文字" * 20
    markdown = f"|{long_cell}|金额|\n|---|---|\n|合计|100|\n"

    result = build_page_blocks(
        page_no=1, markdown=markdown, layout=PageLayout(page_no=1)
    )

    assert [block.kind for block in result.page.blocks] == ["table"]
    assert result.warnings == ()


def test_running_header_is_dropped_across_pages() -> None:
    header = "<u>贵州茅台酒股份有限公司 2024 年年度报告</u>"
    document = merged(
        [
            f"{header}\n\n正文一\n",
            f"{header}\n\n正文二\n",
            f"{header}\n\n正文三\n",
        ]
    )

    blocks = build_document_blocks(document, layouts={})

    texts = [
        block.text
        for page in blocks.pages
        for block in page.blocks
        if block.kind == "text"
    ]
    assert all("贵州茅台酒股份有限公司" not in text for text in texts)
    assert all("正文" in text for text in texts)


def test_unique_underline_line_is_not_dropped() -> None:
    header = "<u>贵州茅台酒股份有限公司 2024 年年度报告</u>"
    document = merged(
        [
            f"{header}\n\n正文一\n",
            f"{header}\n\n正文二\n",
            "<u>只出现一次的图注</u>\n",
        ]
    )

    blocks = build_document_blocks(document, layouts={})

    texts = [
        block.text
        for page in blocks.pages
        for block in page.blocks
        if block.kind == "text"
    ]
    assert any("只出现一次的图注" in text for text in texts)


def test_item_path_drops_running_header_line() -> None:
    header = "贵州茅台酒股份有限公司 2024 年年度报告"
    layouts = {
        page: PageLayout(
            page_no=page,
            items=(
                item(header, 50, 800, width=300),
                item(f"正文{page}", 50, 700, width=60),
            ),
        )
        for page in (1, 2, 3)
    }
    document = merged(
        [f"<u>{header}</u>\n\n正文{page}\n" for page in (1, 2, 3)]
    )

    blocks = build_document_blocks(document, layouts=layouts)

    texts = [
        block.text
        for page in blocks.pages
        for block in page.blocks
        if block.kind == "text"
    ]
    assert all(header not in text for text in texts)
    assert any("正文1" in text for text in texts)


def test_selected_grid_is_logically_merged(monkeypatch) -> None:
    grid = TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 1.0, 1.0),
        n_rows=3,
        n_cols=3,
        header_rows=1,
        cells=(
            TableCell(row=0, col=0, text="姓名"),
            TableCell(row=0, col=1, text="职务"),
            TableCell(row=0, col=2, text="任期起始"),
            TableCell(row=1, col=0, text="张德芹", rowspan=2),
            TableCell(row=1, col=1, text="党委书记"),
            TableCell(row=1, col=2, text="2024-04-30"),
            TableCell(row=2, col=1, text="董事长、董事"),
            TableCell(row=2, col=2, text="2024-05-29"),
        ),
        source_engine="test",
        confidence=1.0,
    )
    routing = TableRouting(
        tables=(
            TableChoice(
                grid=grid,
                source="ruled",
                reason="accepted",
                quality=grid_quality(grid),
            ),
        )
    )
    monkeypatch.setattr(
        build_module, "route_tables", lambda *args, **kwargs: routing
    )

    result = build_page_blocks(
        page_no=1, markdown="正文\n", layout=PageLayout(page_no=1)
    )

    tables = [block for block in result.page.blocks if block.kind == "table"]
    assert len(tables) == 1
    assert tables[0].table is not None
    assert tables[0].table.grid.n_rows == 2
