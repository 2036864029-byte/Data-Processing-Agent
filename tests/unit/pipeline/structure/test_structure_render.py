"""PR-1 块渲染测试：标题 / 正文 / 表题行 + 锚点 + HTML / 缺口回退。"""

from __future__ import annotations

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.structure.models import (
    Block,
    DocumentBlocks,
    PageBlocks,
    TableSlot,
)
from dataagent.pipeline.structure.render import render_document_markdown


def grid() -> TableGrid:
    return TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 10.0, 10.0),
        n_rows=1,
        n_cols=2,
        header_rows=1,
        cells=(
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额"),
        ),
        source_engine="test",
        confidence=1.0,
    )


def slot(*, table_id: str, caption: str | None) -> TableSlot:
    return TableSlot(
        grid=grid(),
        bbox=(0.0, 0.0, 10.0, 10.0),
        source="ruled",
        caption=caption,
        table_id=table_id,
    )


def test_renders_heading_text_and_table_anchor() -> None:
    blocks = DocumentBlocks(
        doc_id="doc",
        pages=(
            PageBlocks(
                page_no=1,
                blocks=(
                    Block(kind="heading", page_no=1, bbox=(0, 0, 1, 1), text="标题", level=1),
                    Block(kind="text", page_no=1, bbox=(0, 0, 1, 1), text="正文"),
                    Block(
                        kind="table",
                        page_no=1,
                        bbox=(0, 0, 10, 10),
                        table=slot(table_id="t001", caption="表 3-1 合计"),
                    ),
                ),
            ),
        ),
    )

    rendered = render_document_markdown(blocks, fallback={})

    assert "# 标题" in rendered
    assert "正文" in rendered
    assert "表 3-1 合计（t001）" in rendered
    assert "<!--table:t001-->" in rendered
    assert "<table>" in rendered
    assert rendered.count("<!--table:t001-->") == 1


def test_table_without_caption_renders_id_line() -> None:
    blocks = DocumentBlocks(
        doc_id="doc",
        pages=(
            PageBlocks(
                page_no=1,
                blocks=(
                    Block(
                        kind="table",
                        page_no=1,
                        bbox=(0, 0, 10, 10),
                        table=slot(table_id="t002", caption=None),
                    ),
                ),
            ),
        ),
    )

    rendered = render_document_markdown(blocks, fallback={})

    assert "（t002）" in rendered
    assert "<!--table:t002-->" in rendered


def test_gap_page_uses_fallback_markdown() -> None:
    blocks = DocumentBlocks(
        doc_id="doc",
        pages=(
            PageBlocks(page_no=1),
            PageBlocks(
                page_no=2,
                blocks=(Block(kind="text", page_no=2, bbox=(0, 0, 1, 1), text="正文"),),
            ),
        ),
    )

    rendered = render_document_markdown(
        blocks,
        fallback={1: "<!-- dataagent:gap page=1 -->\n"},
    )

    assert "<!-- dataagent:gap page=1 -->" in rendered
    assert rendered.index("gap page=1") < rendered.index("正文")


def test_document_order_is_preserved() -> None:
    blocks = DocumentBlocks(
        doc_id="doc",
        pages=(
            PageBlocks(
                page_no=1,
                blocks=(Block(kind="text", page_no=1, bbox=(0, 0, 1, 1), text="第一页"),),
            ),
            PageBlocks(
                page_no=2,
                blocks=(Block(kind="text", page_no=2, bbox=(0, 0, 1, 1), text="第二页"),),
            ),
        ),
    )

    rendered = render_document_markdown(blocks, fallback={})

    assert rendered.index("第一页") < rendered.index("第二页")
