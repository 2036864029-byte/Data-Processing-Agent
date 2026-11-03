"""表格路由测试：L0/L1 择优、平手优先 Markdown、多表配对。"""

from __future__ import annotations

from dataagent.pipeline.table.geometry import PositionedText
from dataagent.pipeline.table.router import route_tables


def item(text: str, x: float, y: float, *, width: float = 40.0) -> PositionedText:
    return PositionedText(text=text, x=x, y=y, width=width, height=10.0)


def good_geometry_items() -> list[PositionedText]:
    return [
        item("项目", 0, 330),
        item("金额", 50, 330),
        item("合计", 0, 310),
        item("100", 50, 310),
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
        item("1", 0, 310),
        item("2", 50, 310),
        item("c", 0, 200),
        item("d", 50, 200),
        item("3", 0, 180),
        item("4", 50, 180),
    ]

    routing = route_tables(markdown, items, page_no=1)

    assert routing.markdown_count == 2
    assert routing.geometry_count == 2
    assert len(routing.tables) == 2
    assert all(choice.source == "markdown" for choice in routing.tables)


def test_extra_geometry_fragments_are_ignored_when_markdown_exists() -> None:
    items = [
        *good_geometry_items(),
        item("c", 0, 200),
        item("d", 50, 200),
        item("3", 0, 180),
        item("4", 50, 180),
    ]

    routing = route_tables(good_markdown(), items, page_no=1)

    assert routing.markdown_count == 1
    assert routing.geometry_count == 2
    assert len(routing.tables) == 1
    assert routing.tables[0].source == "markdown"


def test_geometry_tables_are_kept_when_no_markdown_table() -> None:
    items = [
        item("a", 0, 330),
        item("b", 50, 330),
        item("1", 0, 310),
        item("2", 50, 310),
        item("c", 0, 200),
        item("d", 50, 200),
        item("3", 0, 180),
        item("4", 50, 180),
    ]

    routing = route_tables("", items, page_no=1)

    assert routing.markdown_count == 0
    assert routing.geometry_count == 2
    assert [choice.source for choice in routing.tables] == [
        "geometry",
        "geometry",
    ]
