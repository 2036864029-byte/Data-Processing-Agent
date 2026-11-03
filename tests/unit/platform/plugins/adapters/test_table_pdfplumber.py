"""pdfplumber 有框线表格 → TableGrid 转换测试（不依赖真实 PDF）。"""

from __future__ import annotations

from typing import Any

from dataagent.platform.plugins.adapters.table_pdfplumber import (
    PdfplumberTableSource,
    grid_from_table,
)


class _Table:
    def __init__(
        self,
        *,
        bbox: tuple[float, float, float, float],
        cells: list[tuple[float, float, float, float]],
        matrix: list[list[str | None]],
    ) -> None:
        self.bbox = bbox
        self.cells = cells
        self._matrix = matrix

    def extract(self) -> list[list[str | None]]:
        return self._matrix


def _bbox_cell(
    x0: float, top: float, x1: float, bottom: float
) -> tuple[float, float, float, float]:
    return (x0, top, x1, bottom)


def test_grid_from_table_builds_cells_and_converts_y_axis() -> None:
    table: Any = _Table(
        bbox=(0.0, 10.0, 20.0, 30.0),
        cells=[
            _bbox_cell(0.0, 10.0, 10.0, 20.0),
            _bbox_cell(10.0, 10.0, 20.0, 20.0),
            _bbox_cell(0.0, 20.0, 10.0, 30.0),
            _bbox_cell(10.0, 20.0, 20.0, 30.0),
        ],
        matrix=[["A", "B"], ["C", "D"]],
    )

    grid = grid_from_table(table, page_no=1, page_height=100.0)

    assert grid is not None
    assert (grid.n_rows, grid.n_cols) == (2, 2)
    assert grid.bbox == (0.0, 70.0, 20.0, 90.0)
    assert grid.header_rows == 1
    assert grid.source_engine == "pdfplumber.lines"
    assert {(cell.row, cell.col, cell.text) for cell in grid.cells} == {
        (0, 0, "A"),
        (0, 1, "B"),
        (1, 0, "C"),
        (1, 1, "D"),
    }
    assert grid.cells[0].bbox == (0.0, 80.0, 10.0, 90.0)


def test_grid_from_table_infers_merged_cell_spans() -> None:
    table: Any = _Table(
        bbox=(0.0, 10.0, 20.0, 30.0),
        cells=[
            _bbox_cell(0.0, 10.0, 10.0, 30.0),
            _bbox_cell(10.0, 10.0, 20.0, 20.0),
            _bbox_cell(10.0, 20.0, 20.0, 30.0),
        ],
        matrix=[["A", "B"], ["", "C"]],
    )

    grid = grid_from_table(table, page_no=1, page_height=100.0)

    assert grid is not None
    merged = next(cell for cell in grid.cells if cell.text == "A")
    assert merged.rowspan == 2
    assert merged.colspan == 1
    assert grid.n_rows == 2
    assert grid.n_cols == 2


def test_grid_from_table_clusters_near_identical_edges() -> None:
    table: Any = _Table(
        bbox=(0.0, 10.0, 20.0, 30.0),
        cells=[
            _bbox_cell(0.0, 10.0, 10.0, 20.0),
            _bbox_cell(10.0004, 10.0, 20.0, 20.0),
            _bbox_cell(0.0, 20.0, 10.0, 30.0),
            _bbox_cell(10.0004, 20.0, 20.0, 30.0),
        ],
        matrix=[["A", "B"], ["C", "D"]],
    )

    grid = grid_from_table(table, page_no=1, page_height=100.0)

    assert grid is not None
    assert grid.n_cols == 2


def test_grid_from_table_rejects_degenerate_table() -> None:
    table: Any = _Table(
        bbox=(0.0, 10.0, 10.0, 20.0),
        cells=[_bbox_cell(0.0, 10.0, 10.0, 20.0)],
        matrix=[["A"]],
    )

    assert grid_from_table(table, page_no=1, page_height=100.0) is None


def test_grid_from_table_keeps_multiline_cell_text() -> None:
    table: Any = _Table(
        bbox=(0.0, 10.0, 20.0, 30.0),
        cells=[
            _bbox_cell(0.0, 10.0, 10.0, 20.0),
            _bbox_cell(10.0, 10.0, 20.0, 20.0),
            _bbox_cell(0.0, 20.0, 10.0, 30.0),
            _bbox_cell(10.0, 20.0, 20.0, 30.0),
        ],
        matrix=[["A\nB", "C"], ["D", None]],
    )

    grid = grid_from_table(table, page_no=1, page_height=100.0)

    assert grid is not None
    texts = {cell.text for cell in grid.cells}
    assert "A\nB" in texts
    assert len(grid.cells) == 3


class _FakePage:
    def __init__(self, tables: list[Any]) -> None:
        self._tables = tables
        self.height = 100.0
        self.flushed = False
        self.settings: dict[str, str] | None = None

    def find_tables(self, table_settings: dict[str, str]) -> list[Any]:
        self.settings = table_settings
        return self._tables

    def flush_cache(self) -> None:
        self.flushed = True


class _FakePdf:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_source_extracts_page_and_flushes_cache(monkeypatch: Any) -> None:
    table = _Table(
        bbox=(0.0, 10.0, 20.0, 30.0),
        cells=[
            _bbox_cell(0.0, 10.0, 10.0, 20.0),
            _bbox_cell(10.0, 10.0, 20.0, 20.0),
            _bbox_cell(0.0, 20.0, 10.0, 30.0),
            _bbox_cell(10.0, 20.0, 20.0, 30.0),
        ],
        matrix=[["A", "B"], ["C", "D"]],
    )
    page = _FakePage([table])
    pdf = _FakePdf([page])
    monkeypatch.setattr(
        "dataagent.platform.plugins.adapters.table_pdfplumber.pdfplumber.open",
        lambda source: pdf,
    )

    with PdfplumberTableSource("x.pdf") as source:
        grids = source.extract_page(1)

    assert len(grids) == 1
    assert page.flushed is True
    assert page.settings is not None
    assert pdf.closed is True


def test_source_returns_empty_for_out_of_range_page(monkeypatch: Any) -> None:
    page = _FakePage([])
    pdf = _FakePdf([page])
    monkeypatch.setattr(
        "dataagent.platform.plugins.adapters.table_pdfplumber.pdfplumber.open",
        lambda source: pdf,
    )

    with PdfplumberTableSource("x.pdf") as source:
        assert source.extract_page(0) == ()
        assert source.extract_page(2) == ()


def test_source_accepts_bytes(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_open(source: Any) -> _FakePdf:
        captured["source"] = source
        return _FakePdf([_FakePage([])])

    monkeypatch.setattr(
        "dataagent.platform.plugins.adapters.table_pdfplumber.pdfplumber.open",
        fake_open,
    )

    source = PdfplumberTableSource(b"%PDF-1.4")
    try:
        assert hasattr(captured["source"], "read")
    finally:
        source.close()
