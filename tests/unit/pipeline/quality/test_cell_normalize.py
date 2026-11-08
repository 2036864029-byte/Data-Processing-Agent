"""表格单元格文本归一化测试（Phase 2）。

目标：单元格里的 CJK 伪空格被删除、行尾硬换行按语义合并，
但跨单元格不合并、结构字段不变、函数幂等。
"""

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.quality.text_normalize import (
    normalize_cell_text,
    normalize_table_grid,
)


def test_phantom_spaces_removed() -> None:
    assert normalize_cell_text("中 文") == "中文"
    assert normalize_cell_text("公 司 章 程") == "公司章程"
    assert normalize_cell_text("姓  名") == "姓名"


def test_fullwidth_space_removed() -> None:
    assert normalize_cell_text("中　文") == "中文"


def test_latin_spaces_are_preserved() -> None:
    assert normalize_cell_text("John Smith") == "John Smith"
    assert normalize_cell_text("1 000") == "1 000"


def test_intra_cell_hard_wrap_is_joined() -> None:
    assert normalize_cell_text("第一行\n第二行") == "第一行第二行"


def test_semantic_newline_after_terminal_punctuation_is_kept() -> None:
    assert normalize_cell_text("第一项。\n第二项。") == "第一项。\n第二项。"


def test_numbered_lines_stay_separate() -> None:
    text = "条件：\n（1）甲\n（2）乙"

    assert normalize_cell_text(text) == text


def test_numeric_lines_are_not_joined() -> None:
    assert normalize_cell_text("2024\n2025") == "2024\n2025"


def test_outer_whitespace_is_stripped() -> None:
    assert normalize_cell_text("  正文  ") == "正文"
    assert normalize_cell_text("") == ""
    assert normalize_cell_text("   ") == ""


def test_cell_normalization_is_idempotent() -> None:
    for text in ("中 文\n继 续", "第一项。\n第二项。", "2024\n2025", ""):
        once = normalize_cell_text(text)
        assert normalize_cell_text(once) == once


def test_table_grid_changes_text_only() -> None:
    grid = TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 100.0, 50.0),
        n_rows=2,
        n_cols=2,
        header_rows=1,
        cells=(
            TableCell(row=0, col=0, text="姓 名"),
            TableCell(row=0, col=1, text="职 务"),
            TableCell(row=1, col=0, text="张 三", rowspan=1, colspan=1),
            TableCell(row=1, col=1, text="董 事\n长"),
        ),
        source_engine="test",
        confidence=0.9,
    )

    normalized = normalize_table_grid(grid)

    assert [cell.text for cell in normalized.cells] == [
        "姓名",
        "职务",
        "张三",
        "董事长",
    ]
    assert [
        (cell.row, cell.col, cell.rowspan, cell.colspan) for cell in normalized.cells
    ] == [
        (cell.row, cell.col, cell.rowspan, cell.colspan) for cell in grid.cells
    ]
    assert normalized.n_rows == grid.n_rows
    assert normalized.n_cols == grid.n_cols
    assert normalized.header_rows == grid.header_rows
    assert normalized.bbox == grid.bbox


def test_table_grid_normalization_is_idempotent() -> None:
    grid = TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 100.0, 50.0),
        n_rows=1,
        n_cols=1,
        header_rows=0,
        cells=(TableCell(row=0, col=0, text="中 文\n内 容"),),
        source_engine="test",
        confidence=0.9,
    )

    once = normalize_table_grid(grid)

    assert normalize_table_grid(once) == once
