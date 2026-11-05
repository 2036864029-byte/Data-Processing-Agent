"""网格级跨页表合并测试：边界、表头、列数、缺口与链式合并。"""

from __future__ import annotations

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.structure.crosspage import merge_cross_page_tables
from dataagent.pipeline.structure.models import Block, PageBlocks, TableSlot


def table_block(
    page_no: int,
    header: list[str],
    rows: list[list[str]],
    *,
    page_span: tuple[int, int] | None = None,
) -> Block:
    cells = [
        TableCell(row=0, col=col, text=text)
        for col, text in enumerate(header)
    ]
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            if text:
                cells.append(TableCell(row=row_index, col=col, text=text))
    grid = TableGrid(
        page_no=page_no,
        page_span=page_span or (page_no, page_no),
        bbox=(0.0, 0.0, 10.0, 10.0),
        n_rows=len(rows) + 1,
        n_cols=len(header),
        header_rows=1,
        cells=tuple(cells),
        source_engine="test",
        confidence=1.0,
    )
    slot = TableSlot(grid=grid, bbox=grid.bbox, source="ruled")
    return Block(kind="table", page_no=page_no, bbox=grid.bbox, table=slot)


def text_block(page_no: int, text: str) -> Block:
    return Block(kind="text", page_no=page_no, bbox=(0, 0, 1, 1), text=text)


def page(page_no: int, blocks: list[Block], *, gap: bool = False) -> PageBlocks:
    return PageBlocks(page_no=page_no, blocks=tuple(blocks), gap=gap)


def tables(pages: tuple[PageBlocks, ...]) -> list[TableGrid]:
    return [
        block.table.grid
        for item in pages
        for block in item.blocks
        if block.kind == "table" and block.table is not None
    ]


def test_merges_continuation_table() -> None:
    pages = merge_cross_page_tables(
        (
            page(1, [text_block(1, "前文"), table_block(1, ["a", "b"], [["1", "2"]])]),
            page(2, [table_block(2, ["a", "b"], [["3", "4"]]), text_block(2, "后文")]),
        )
    )

    grids = tables(pages)
    assert len(grids) == 1
    assert grids[0].n_rows == 3
    assert grids[0].page_span == (1, 2)
    assert {cell.text for cell in grids[0].cells} == {"a", "b", "1", "2", "3", "4"}
    assert [block.kind for block in pages[1].blocks] == ["text"]


def test_three_page_chain_merges_into_first_page() -> None:
    pages = merge_cross_page_tables(
        (
            page(1, [table_block(1, ["a", "b"], [["1", "2"]])]),
            page(2, [table_block(2, ["a", "b"], [["3", "4"]])]),
            page(3, [table_block(3, ["a", "b"], [["5", "6"]])]),
        )
    )

    grids = tables(pages)
    assert len(grids) == 1
    assert grids[0].n_rows == 4
    assert grids[0].page_span == (1, 3)
    assert pages[1].blocks == ()
    assert pages[2].blocks == ()


def test_different_columns_are_not_merged() -> None:
    pages = merge_cross_page_tables(
        (
            page(1, [table_block(1, ["a", "b"], [["1", "2"]])]),
            page(2, [table_block(2, ["a", "b", "c"], [["3", "4", "5"]])]),
        )
    )

    assert len(tables(pages)) == 2


def test_different_header_is_not_merged() -> None:
    pages = merge_cross_page_tables(
        (
            page(1, [table_block(1, ["项目", "金额"], [["1", "2"]])]),
            page(2, [table_block(2, ["姓名", "职务"], [["3", "4"]])]),
        )
    )

    assert len(tables(pages)) == 2


def test_table_not_at_boundary_is_not_merged() -> None:
    pages = merge_cross_page_tables(
        (
            page(1, [table_block(1, ["a", "b"], [["1", "2"]]), text_block(1, "页尾")]),
            page(2, [text_block(2, "页首"), table_block(2, ["a", "b"], [["3", "4"]])]),
        )
    )

    assert len(tables(pages)) == 2


def test_gap_page_breaks_chain() -> None:
    pages = merge_cross_page_tables(
        (
            page(1, [table_block(1, ["a", "b"], [["1", "2"]])]),
            page(2, [], gap=True),
            page(3, [table_block(3, ["a", "b"], [["3", "4"]])]),
        )
    )

    assert len(tables(pages)) == 2


def test_header_only_continuation_is_not_merged() -> None:
    pages = merge_cross_page_tables(
        (
            page(1, [table_block(1, ["a", "b"], [["1", "2"]])]),
            page(2, [table_block(2, ["a", "b"], [])]),
        )
    )

    assert len(tables(pages)) == 2
