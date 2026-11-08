"""单元格归一化的性质测试（泛化保证）。

真实语料只能证明“这几页没问题”，保证不了输入域上的正确性。
这里对 `normalize_cell_text` / `normalize_table_grid` 生成式断言：

- 幂等：`f(f(x)) == f(x)`；
- 不增不减：非空白字符序列前后一致（只搬动空白，不生成/删除内容）；
- 归一彻底：结果中不存在两个 CJK 字符之间的空白；
- 形状稳定：结果无首尾空白、无空行；
- 纯 ASCII 多行不合并（只做逐行 strip）；
- 表格网格：结构字段不变、单元格之间互不污染、整体幂等。
"""

from __future__ import annotations

import re

import hypothesis.strategies as st
from hypothesis import given, settings
from hypothesis.strategies import DrawFn

from dataagent.contracts.table import TableCell, TableGrid
from dataagent.pipeline.quality.text_normalize import (
    normalize_cell_text,
    normalize_table_grid,
)

# 独立于实现的判定式（刻意不 import 私有正则）：归一后不应再有 CJK 间空白。
_CJK_CLASS = (
    "㐀-䶿一-鿿"
    "，。！？；：、（）「」『』【】《》〈〉…—"
)
_RESIDUAL_CJK_SPACE = re.compile(
    rf"(?<=[{_CJK_CLASS}])[ \t　]+(?=[{_CJK_CLASS}])"
)
_ASCII_ONLY = st.text(
    alphabet=st.sampled_from(list("abcXYZ019 \t\n.,;:()[]{}-/"))
)
_MIXED = st.text(
    alphabet=st.sampled_from(
        list("甲乙丙丁戊己庚辛壬癸中文章节项条款规定")
        + list("abcXYZ019")
        + list(" \t　\n\r")
        + list("，。！？；：、（）「」")
        + list("#-*")
    )
)


@settings(max_examples=200, deadline=None)
@given(text=_MIXED)
def test_cell_normalization_is_idempotent(text: str) -> None:
    once = normalize_cell_text(text)

    assert normalize_cell_text(once) == once


@settings(max_examples=200, deadline=None)
@given(text=_MIXED)
def test_cell_normalization_preserves_non_space_characters(text: str) -> None:
    """只搬动空白：非空白字符序列必须完全一致。"""
    assert "".join(normalize_cell_text(text).split()) == "".join(text.split())


@settings(max_examples=200, deadline=None)
@given(text=_MIXED)
def test_cell_normalization_leaves_no_cjk_spaces(text: str) -> None:
    assert _RESIDUAL_CJK_SPACE.search(normalize_cell_text(text)) is None


@settings(max_examples=200, deadline=None)
@given(text=_MIXED)
def test_cell_normalization_has_clean_shape(text: str) -> None:
    result = normalize_cell_text(text)

    if not result:
        return
    assert result == result.strip()
    assert all(line and line == line.strip() for line in result.split("\n"))


@settings(max_examples=200, deadline=None)
@given(text=_ASCII_ONLY)
def test_ascii_only_cell_lines_are_never_joined(text: str) -> None:
    """纯数字/拉丁多行只做逐行 strip，不合并（如 2024 / 2025 两行日期）。"""
    expected = "\n".join(
        line.strip() for line in text.split("\n") if line.strip()
    )

    assert normalize_cell_text(text) == expected


_CELL_TEXT = st.text(
    alphabet=st.sampled_from(
        list("甲乙丙丁中文章节项规定")
        + list("abc019")
        + list(" \t　\n")
        + list("，。；、")
    ),
    max_size=40,
)


@st.composite
def _grids(draw: DrawFn) -> TableGrid:
    n_rows = draw(st.integers(min_value=1, max_value=5))
    n_cols = draw(st.integers(min_value=1, max_value=5))
    texts = draw(
        st.lists(_CELL_TEXT, min_size=n_rows * n_cols, max_size=n_rows * n_cols)
    )
    return TableGrid(
        page_no=1,
        bbox=(0.0, 0.0, 100.0, 50.0),
        n_rows=n_rows,
        n_cols=n_cols,
        header_rows=0,
        cells=tuple(
            TableCell(
                row=index // n_cols,
                col=index % n_cols,
                text=f"⟦{index}⟧{text}",
            )
            for index, text in enumerate(texts)
        ),
        source_engine="property",
        confidence=0.5,
    )


@settings(max_examples=100, deadline=None)
@given(grid=_grids())
def test_table_grid_keeps_structure(grid: TableGrid) -> None:
    normalized = normalize_table_grid(grid)

    assert [
        (cell.row, cell.col, cell.rowspan, cell.colspan, cell.bbox)
        for cell in normalized.cells
    ] == [
        (cell.row, cell.col, cell.rowspan, cell.colspan, cell.bbox)
        for cell in grid.cells
    ]
    assert normalized.n_rows == grid.n_rows
    assert normalized.n_cols == grid.n_cols
    assert normalized.header_rows == grid.header_rows
    assert normalized.bbox == grid.bbox


@settings(max_examples=100, deadline=None)
@given(grid=_grids())
def test_table_grid_cells_are_not_cross_contaminated(grid: TableGrid) -> None:
    """每个单元格只按自己的文本归一，绝不串到别的单元格。"""
    normalized = normalize_table_grid(grid)
    texts = [cell.text for cell in normalized.cells]

    assert len(texts) == len(grid.cells)
    for index, text in enumerate(texts):
        assert text.startswith(f"⟦{index}⟧")
        assert all(
            f"⟦{other}⟧" not in text
            for other in range(len(texts))
            if other != index
        )


@settings(max_examples=100, deadline=None)
@given(grid=_grids())
def test_table_grid_normalization_is_idempotent(grid: TableGrid) -> None:
    once = normalize_table_grid(grid)

    assert normalize_table_grid(once) == once
