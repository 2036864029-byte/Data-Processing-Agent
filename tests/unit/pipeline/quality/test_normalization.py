"""统一归一化入口测试（Phase 3）。

三段流水：抽取文本 → 归一化 → 建块/渲染。Markdown 路径、坐标路径、表格路径
必须同一口径、同一幂等性；消费方不得再各自组合 `normalize_cjk_text` 与
`repair_numbering`。
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.quality.normalization import (
    normalize_cell,
    normalize_table_grid,
    normalize_text,
)
from dataagent.pipeline.structure.blocks import join_lines

_TEXT_ALPHABET = st.sampled_from(
    list("甲乙丙丁中文章节条款规定")
    + list("abc019 \t\n")
    + list("，。；：、（）().#")
)


def test_normalize_text_composes_spaces_numbering_and_reflow() -> None:
    result = normalize_text("中 文 2（）\n继 续")

    assert result == "中文（2）继续"


def test_normalize_text_keeps_table_blocks_untouched() -> None:
    markdown = "| 中 文 | 2（） |\n| --- | --- |\n| 甲 | 乙 |\n"

    assert normalize_text(markdown) == markdown


def test_normalize_text_leaves_english_parentheses_alone() -> None:
    text = "The item (2) is fine.\n"

    assert normalize_text(text) == text


def test_numbering_repair_applies_on_coordinate_path() -> None:
    """坐标路径过去只做伪空格，编号错序修不掉；统一入口后两条路径一致。"""
    lines = ["1 答：（）", "内容"]

    body = normalize_text("\n".join(lines))
    coordinate = join_lines(lines)

    assert body == coordinate == "答：（1）内容"
    assert "1 答：" not in coordinate


def test_normalize_cell_keeps_semantic_lines() -> None:
    assert normalize_cell("姓 名\n（1）职 务") == "姓名\n（1）职务"
    assert normalize_cell("2024\n2025") == "2024\n2025"


def test_normalize_table_grid_entry_matches_cell_entry() -> None:
    grid = TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 10.0, 5.0),
        n_rows=1,
        n_cols=1,
        header_rows=0,
        cells=(TableCell(row=0, col=0, text="姓 名\n职 务"),),
        source_engine="test",
        confidence=0.5,
    )

    normalized = normalize_table_grid(grid)

    assert normalized.cells[0].text == normalize_cell(grid.cells[0].text)


@settings(max_examples=200, deadline=None)
@given(text=st.text(alphabet=_TEXT_ALPHABET))
def test_normalize_text_is_idempotent(text: str) -> None:
    once = normalize_text(text)

    assert normalize_text(once) == once
