"""表格指标解析测试：填充率、列数方差、表头有效性（qc-output.md 口径）。"""

from __future__ import annotations

import pytest

from dataagent.pipeline.quality.table_metrics import table_page_metrics


def test_well_formed_table() -> None:
    metrics = table_page_metrics(
        "intro\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
    )
    assert metrics is not None
    assert metrics.tables == 1
    assert metrics.cell_fill == pytest.approx(1.0)
    assert metrics.column_variance == pytest.approx(0.0)
    assert metrics.header_ok is True


def test_empty_header_is_flagged() -> None:
    metrics = table_page_metrics("| | |\n|---|---|\n| 1 | 2 |\n")
    assert metrics is not None
    assert metrics.header_ok is False
    assert metrics.cell_fill == pytest.approx(0.5)


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
    assert metrics.cell_fill == pytest.approx(0.5)


def test_separator_variants_accepted() -> None:
    metrics = table_page_metrics(
        "| a | b |\n|:---:|---:|\n| 1 | 2 |\n"
    )
    assert metrics is not None
    assert metrics.header_ok is True


def test_no_table_returns_none() -> None:
    assert table_page_metrics("plain text only\nno pipes\n") is None
