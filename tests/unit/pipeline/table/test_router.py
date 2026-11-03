"""表格路由测试：L0/L1 择优、平手优先 Markdown、多表配对。"""

from __future__ import annotations

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.table.geometry import PositionedText
from dataagent.pipeline.table.router import route_tables


def item(text: str, x: float, y: float, *, width: float = 40.0) -> PositionedText:
    return PositionedText(text=text, x=x, y=y, width=width, height=10.0)


def good_geometry_items() -> list[PositionedText]:
    return [
        item("项目", 0, 330),
        item("金额", 50, 330),
        item("合计", 0, 314),
        item("100", 50, 314),
    ]


def good_markdown() -> str:
    return "| 项目 | 金额 |\n|---|---|\n| 合计 | 100 |\n"


def test_geometry_wins_when_markdown_header_missing() -> None:
    markdown = "| | |\n|---|---|\n| 项目 | 金额 |\n| 合计 | 100 |\n"

    routing = route_tables(markdown, good_geometry_items(), page_no=1)

    assert routing.tables[0].source == "geometry"
    assert routing.tables[0].quality.cell_fill == 1.0


def test_markdown_only_when_no_geometry() -> None:
    routing = route_tables(good_markdown(), [], page_no=1)

    assert len(routing.tables) == 1
    assert routing.tables[0].source == "markdown"
    assert routing.tables[0].reason == "only_l0"


def test_markdown_wins_on_tie() -> None:
    routing = route_tables(good_markdown(), good_geometry_items(), page_no=1)

    assert routing.tables[0].source == "markdown"


def test_multiple_tables_are_paired_by_order() -> None:
    markdown = (
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "| c | d |\n|---|---|\n| 3 | 4 |\n"
    )
    items = [
        item("a", 0, 330),
        item("b", 50, 330),
        item("1", 0, 314),
        item("2", 50, 314),
        item("c", 0, 200),
        item("d", 50, 200),
        item("3", 0, 184),
        item("4", 50, 184),
    ]

    routing = route_tables(markdown, items, page_no=1)

    assert routing.markdown_count == 2
    assert routing.geometry_count == 2
    assert len(routing.tables) == 2
    assert all(choice.source == "markdown" for choice in routing.tables)


def test_unpaired_geometry_table_is_kept_when_not_overlapping_l0() -> None:
    items = [
        *good_geometry_items(),
        item("c", 0, 200),
        item("d", 50, 200),
        item("3", 0, 184),
        item("4", 50, 184),
    ]

    routing = route_tables(good_markdown(), items, page_no=1)

    assert routing.markdown_count == 1
    assert routing.geometry_count == 2
    assert len(routing.tables) == 2
    assert [choice.source for choice in routing.tables] == [
        "markdown",
        "geometry",
    ]


def test_geometry_tables_are_kept_when_no_markdown_table() -> None:
    items = [
        item("a", 0, 330),
        item("b", 50, 330),
        item("1", 0, 314),
        item("2", 50, 314),
        item("c", 0, 200),
        item("d", 50, 200),
        item("3", 0, 184),
        item("4", 50, 184),
    ]

    routing = route_tables("", items, page_no=1)

    assert routing.markdown_count == 0
    assert routing.geometry_count == 2
    assert [choice.source for choice in routing.tables] == [
        "geometry",
        "geometry",
    ]


def test_pairing_uses_bbox_not_index_order() -> None:
    markdown = (
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"  # 上方表：正常
        "| | |\n|---|---|\n| c | d |\n"  # 下方表：表头缺失
    )
    items = [
        item("c", 0, 200),
        item("d", 50, 200),
        item("e", 0, 184),
        item("f", 50, 184),
    ]

    routing = route_tables(markdown, items, page_no=1)

    assert len(routing.tables) == 2
    sources = [choice.source for choice in routing.tables]
    assert sources.count("markdown") == 1
    assert sources.count("geometry") == 1
    markdown_choice = next(
        choice for choice in routing.tables if choice.source == "markdown"
    )
    assert {cell.text for cell in markdown_choice.grid.cells} == {
        "a",
        "b",
        "1",
        "2",
    }


def ruled_grid(
    rows: list[list[str]],
    *,
    bbox: tuple[float, float, float, float] = (0.0, 314.0, 90.0, 340.0),
) -> TableGrid:
    cells = tuple(
        TableCell(row=row_index, col=col_index, text=text)
        for row_index, row in enumerate(rows)
        for col_index, text in enumerate(row)
        if text
    )
    return TableGrid(
        page_no=1,
        bbox=bbox,
        n_rows=len(rows),
        n_cols=max((len(row) for row in rows), default=1),
        header_rows=1,
        cells=cells,
        source_engine="pdfplumber.lines",
        confidence=0.95,
    )


def test_ruled_table_wins_over_markdown_when_accepted() -> None:
    ruled = ruled_grid([["项目", "金额"], ["合计", "100"]])

    routing = route_tables(
        good_markdown(), good_geometry_items(), page_no=1, ruled_tables=[ruled]
    )

    assert routing.ruled_count == 1
    assert [choice.source for choice in routing.tables] == ["ruled"]


def test_rejected_ruled_table_falls_back_to_markdown() -> None:
    ruled = ruled_grid(
        [["项目", "金额"], ["合计", "2024年05月29日 2023年08月17日"]]
    )

    routing = route_tables(good_markdown(), [], page_no=1, ruled_tables=[ruled])

    assert routing.ruled_count == 1
    assert routing.rejected_ruled == ("multi_value_cell",)
    assert [choice.source for choice in routing.tables] == ["markdown"]


def test_accepted_ruled_table_suppresses_overlapping_geometry() -> None:
    ruled = ruled_grid([["项目", "金额"], ["合计", "100"]])

    routing = route_tables("", good_geometry_items(), page_no=1, ruled_tables=[ruled])

    assert routing.geometry_count == 1
    assert [choice.source for choice in routing.tables] == ["ruled"]


def test_unpaired_ruled_table_is_emitted_without_markdown() -> None:
    ruled = ruled_grid([["a", "b"], ["1", "2"]])

    routing = route_tables("", [], page_no=1, ruled_tables=[ruled])

    assert routing.ruled_count == 1
    assert [choice.source for choice in routing.tables] == ["ruled"]
    assert routing.tables[0].reason == "accepted"


def test_accepted_ruled_suppresses_only_duplicate_l0_tables() -> None:
    markdown = (
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "| c | d |\n|---|---|\n| 3 | 4 |\n"
    )
    ruled = ruled_grid([["a", "b"], ["1", "2"]])

    routing = route_tables(markdown, [], page_no=1, ruled_tables=[ruled])

    sources = [choice.source for choice in routing.tables]
    assert sources.count("ruled") == 1
    assert sources.count("markdown") == 1
    remaining = next(
        choice for choice in routing.tables if choice.source == "markdown"
    )
    assert {cell.text for cell in remaining.grid.cells} == {"c", "d", "3", "4"}
