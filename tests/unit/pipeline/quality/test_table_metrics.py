"""表格指标解析测试：填充率、列数方差、表头有效性（qc-output.md 口径）。"""

from __future__ import annotations

import pytest

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.quality.table_metrics import (
    grid_table_metrics,
    table_page_metrics,
    table_structure_ok,
)


def test_well_formed_table() -> None:
    metrics = table_page_metrics(
        "intro\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
    )
    assert metrics is not None
    assert metrics.tables == 1
    assert metrics.cell_fill == pytest.approx(1.0)
    assert metrics.column_variance == pytest.approx(0.0)
    assert metrics.header_ok is True
    assert metrics.row_occupancy == pytest.approx(1.0)
    assert metrics.col_occupancy == pytest.approx(1.0)


def test_empty_rows_and_columns_lower_occupancy() -> None:
    metrics = table_page_metrics(
        "| a | | |\n|---|---|---|\n| 1 | | |\n| | | |\n"
    )

    assert metrics is not None
    assert metrics.row_occupancy == pytest.approx(2 / 3)
    assert metrics.col_occupancy == pytest.approx(1 / 3)


def test_empty_header_is_flagged() -> None:
    metrics = table_page_metrics("| | |\n|---|---|\n| 1 | 2 |\n")
    assert metrics is not None
    assert metrics.header_ok is False
    # 数据行完整；表头缺失由 header_ok 单独表达
    assert metrics.cell_fill == pytest.approx(1.0)


def test_ragged_rows_lower_fill_and_raise_variance() -> None:
    metrics = table_page_metrics(
        "| a | b |\n|---|---|\n| 1 |\n| 2 | 3 | 4 |\n"
    )
    assert metrics is not None
    assert metrics.header_ok is True
    assert metrics.cell_fill < 1.0
    assert metrics.column_variance > 0.0


def test_multiple_tables_take_worst_case() -> None:
    metrics = table_page_metrics(
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "| | |\n|---|---|\n| 3 | 4 |\n"
    )
    assert metrics is not None
    assert metrics.tables == 2
    assert metrics.header_ok is False
    assert metrics.cell_fill == pytest.approx(1.0)


def test_separator_variants_accepted() -> None:
    metrics = table_page_metrics(
        "| a | b |\n|:---:|---:|\n| 1 | 2 |\n"
    )
    assert metrics is not None
    assert metrics.header_ok is True


def test_no_table_returns_none() -> None:
    assert table_page_metrics("plain text only\nno pipes\n") is None


def _grid(
    cells: tuple[TableCell, ...],
    *,
    n_rows: int = 2,
    n_cols: int = 2,
    header_rows: int = 1,
) -> TableGrid:
    return TableGrid(
        page_no=1,
        page_span=(1, 1),
        bbox=(0.0, 0.0, 10.0, 10.0),
        n_rows=n_rows,
        n_cols=n_cols,
        header_rows=header_rows,
        cells=cells,
        source_engine="test",
        confidence=1.0,
    )


def test_grid_metrics_accepts_sparse_financial_table() -> None:
    grid = _grid(
        (
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额"),
            TableCell(row=1, col=0, text="A"),
            TableCell(row=2, col=1, text="1"),
        ),
        n_rows=3,
    )

    metrics = grid_table_metrics([grid])

    assert metrics is not None
    assert metrics.cell_fill < 0.9
    assert metrics.row_occupancy == pytest.approx(1.0)
    assert metrics.col_occupancy == pytest.approx(1.0)
    assert table_structure_ok(metrics) is True


def test_grid_metrics_rejects_empty_structure() -> None:
    grid = _grid(
        (
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额"),
            TableCell(row=1, col=0, text="A"),
        ),
        n_rows=3,
    )

    metrics = grid_table_metrics([grid])

    assert metrics is not None
    assert metrics.row_occupancy < 1.0
    assert table_structure_ok(metrics) is False


def test_grid_metrics_rejects_missing_header() -> None:
    grid = _grid(
        (
            TableCell(row=1, col=0, text="A"),
            TableCell(row=1, col=1, text="1"),
        )
    )

    metrics = grid_table_metrics([grid])

    assert metrics is not None
    assert metrics.header_ok is False
    assert table_structure_ok(metrics) is False


def test_skip_prose_drops_header_fake_table() -> None:
    """R-0002 形态：页眉 + 整页正文被识别成 2×3 表，L1 必须看不到这张表。"""
    prose = "正文内容。" * 40
    markdown = f"|2026/7/28 21:00|_||\n|---|---|---|\n|{prose}|||\n"

    assert table_page_metrics(markdown) is not None
    assert table_page_metrics(markdown, skip_prose=True) is None


def test_skip_prose_keeps_real_table() -> None:
    markdown = "| 项目 | 金额 |\n| --- | --- |\n| 合计 | 100 |\n"

    metrics = table_page_metrics(markdown, skip_prose=True)

    assert metrics is not None
    assert metrics.tables == 1
    assert metrics.header_ok is True
