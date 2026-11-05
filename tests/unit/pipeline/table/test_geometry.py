"""坐标表格重建测试：行聚类、区域检测、列/跨列与混合页。"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from dataagent.pipeline.table.geometry import (
    PositionedText,
    build_table_grid,
    detect_table_regions,
    from_pdf_inspector,
    items_for_page,
)


def item(
    text: str, x: float, y: float, *, width: float = 40.0, height: float = 10.0
) -> PositionedText:
    return PositionedText(text=text, x=x, y=y, width=width, height=height)


def test_from_pdf_inspector_carries_mcid() -> None:
    raw = SimpleNamespace(text="x", x=1, y=2, width=3, height=4, mcid=7)
    assert from_pdf_inspector(raw).mcid == 7

    untagged = SimpleNamespace(text="x", x=1, y=2, width=3, height=4, mcid=None)
    assert from_pdf_inspector(untagged).mcid is None


def table_items() -> list[PositionedText]:
    return [
        item("上方正文第一行", 0, 360, width=300),
        item("上方正文第二行", 0, 345, width=300),
        item("项目", 0, 330),
        item("金额", 50, 330),
        item("备注", 100, 330),
        item("合计", 0, 314),
        item("100", 50, 314),
        item("正常", 100, 314),
        item("明细", 0, 298),
        item("200", 50, 298),
        item("异常", 100, 298),
        item("下方正文", 0, 270, width=300),
    ]


def test_detect_table_region_in_mixed_page() -> None:
    regions = detect_table_regions(table_items(), page_no=1)

    assert len(regions) == 1
    region = regions[0]
    assert region.page_no == 1
    assert region.confidence == pytest.approx(1.0)
    assert region.bbox[1] == pytest.approx(298)
    assert region.bbox[3] == pytest.approx(340)


def test_build_grid_from_region() -> None:
    region = detect_table_regions(table_items(), page_no=1)[0]

    grid = build_table_grid(region.items, page_no=1, bbox=region.bbox)

    assert (grid.n_rows, grid.n_cols) == (3, 3)
    assert grid.header_rows == 1
    texts = {(cell.row, cell.col): cell.text for cell in grid.cells}
    assert texts[(0, 0)] == "项目"
    assert texts[(1, 1)] == "100"
    assert texts[(2, 2)] == "异常"
    assert grid.source_engine == "pdf-inspector.positions"


def test_single_row_is_not_a_table() -> None:
    items = [
        item("项目", 0, 330),
        item("金额", 50, 330),
        item("备注", 100, 330),
    ]

    assert detect_table_regions(items, page_no=1) == []


def test_colspan_is_detected() -> None:
    items = [
        item("表头一", 0, 330),
        item("表头二", 50, 330),
        item("表头三", 100, 330),
        item("合并单元格", 0, 314, width=90),
        item("值", 100, 314),
        item("a", 0, 298),
        item("b", 50, 298),
        item("c", 100, 298),
    ]

    grid = build_table_grid(items, page_no=1, bbox=(0, 290, 140, 340))

    merged = next(
        cell for cell in grid.cells if cell.text == "合并单元格"
    )
    assert merged.colspan == 2


def test_empty_text_items_are_ignored() -> None:
    items = [
        item(" ", 0, 330),
        item("项目", 0, 330),
        item("金额", 50, 330),
        item("合计", 0, 314),
        item("100", 50, 314),
    ]

    grid = build_table_grid(items, page_no=1, bbox=(0, 310, 90, 340))

    assert all(cell.text.strip() for cell in grid.cells)


def test_build_requires_items() -> None:
    with pytest.raises(ValueError):
        build_table_grid([], page_no=1, bbox=(0, 0, 10, 10))


@dataclass
class RawItem:
    text: str
    x: float
    y: float
    width: float
    height: float
    page: int


def test_items_for_page_uses_one_indexed_pages() -> None:
    raw = [RawItem("a", 0, 0, 1, 1, page=1), RawItem("b", 0, 0, 1, 1, page=2)]

    assert [value.text for value in items_for_page(raw, 1)] == ["a"]
    assert [value.text for value in items_for_page(raw, 2)] == ["b"]
