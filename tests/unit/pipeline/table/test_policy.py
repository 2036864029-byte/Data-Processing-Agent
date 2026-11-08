"""表格策略测试（Phase 4.4）：阈值集中与注入。"""

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.table.acceptance import evaluate_candidate
from dataagent.pipeline.table.policy import (
    DEFAULT_TABLE_POLICY,
    TablePolicy,
    TableSource,
)
from dataagent.pipeline.table.prose import is_prose_cells


def _grid(n_rows: int = 2, n_cols: int = 2) -> TableGrid:
    cells = tuple(
        TableCell(row=row, col=col, text=f"{row}-{col}")
        for row in range(n_rows)
        for col in range(n_cols)
    )
    return TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 100.0, 50.0),
        n_rows=n_rows,
        n_cols=n_cols,
        header_rows=1,
        cells=cells,
        source_engine="test",
        confidence=0.8,
    )


def test_default_policy_keeps_legacy_thresholds() -> None:
    """默认值必须等于迁移前散落在 acceptance/router/prose 的常量。"""
    policy = DEFAULT_TABLE_POLICY

    assert (policy.min_rows, policy.min_cols) == (2, 2)
    assert policy.min_filled_ratio == 0.15
    assert policy.min_text_coverage == 0.85
    assert policy.min_row_ratio == 0.5
    assert policy.selection_margin == 0.05
    assert policy.pair_min_overlap == 0.15
    assert policy.fragment_overlap == 0.5
    assert policy.l0_suppress_coverage == 0.8
    assert policy.prose_cell_min_chars == 120
    assert policy.prose_cell_short_min_chars == 20
    assert policy.prose_table_area_ratio == 0.6
    assert policy.enable_text_strategy is False


def test_table_source_is_string_enum() -> None:
    assert TableSource.MARKDOWN == "markdown"
    assert TableSource.GEOMETRY == "geometry"
    assert TableSource.RULED == "ruled"
    assert {source.value for source in TableSource} == {
        "markdown",
        "geometry",
        "ruled",
    }


def test_acceptance_policy_is_injectable() -> None:
    grid = _grid()

    assert evaluate_candidate(grid, None).accepted is True
    strict = evaluate_candidate(grid, None, policy=TablePolicy(min_rows=3))
    assert strict.accepted is False
    assert strict.reason == "too_small"


def test_prose_policy_is_injectable() -> None:
    cell = "正" * 80 + "。"

    assert is_prose_cells([cell]) is False
    stricter = TablePolicy(prose_cell_min_chars=60)
    assert is_prose_cells([cell], policy=stricter) is True
