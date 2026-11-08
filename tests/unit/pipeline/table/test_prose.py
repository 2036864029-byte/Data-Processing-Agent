"""伪表格判定测试（Phase 4.1）：L1 与建块共用同一判据。"""

from dataagent.pipeline.table.prose import is_prose_cells

SHORT_PROSE_A = "这一段是正文内容，说明相关情况，请予关注。"
SHORT_PROSE_B = "另一段也是正文，说明处理要求，请予关注。"


def test_long_prose_cell_is_prose_table() -> None:
    assert is_prose_cells(["短", "正" * 119 + "。"]) is True


def test_two_short_prose_cells_are_prose_table() -> None:
    assert is_prose_cells([SHORT_PROSE_A, SHORT_PROSE_B]) is True


def test_single_short_prose_cell_is_not_enough() -> None:
    assert is_prose_cells([SHORT_PROSE_A, "董事"]) is False


def test_long_text_without_sentence_punctuation_is_not_prose() -> None:
    """表格里的长描述没有句末标点，不能当成伪表（真实表格常见）。"""
    assert is_prose_cells(["正" * 200]) is False


def test_data_cells_are_not_prose_table() -> None:
    assert is_prose_cells(["姓名", "职务", "2024-05-29", "董事长"]) is False


def test_boundary_below_min_chars_needs_a_second_cell() -> None:
    just_below = "正" * 118 + "。"

    assert is_prose_cells([just_below]) is False
    assert is_prose_cells([just_below, SHORT_PROSE_B]) is True
