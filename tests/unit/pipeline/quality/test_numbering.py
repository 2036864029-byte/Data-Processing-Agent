"""编号/标点错序修复测试。"""

from __future__ import annotations

from dataagent.pipeline.quality.numbering import (
    malformed_numbering_count,
    repair_numbering,
)


def test_digit_then_empty_parens() -> None:
    assert repair_numbering("原因是（2）") == "原因是（2）"
    assert repair_numbering("原因是2（）") == "原因是（2）"
    assert repair_numbering("原因是2()") == "原因是（2）"


def test_empty_parens_then_digit() -> None:
    assert repair_numbering("（）2 条款") == "（2） 条款"


def test_spaced_and_mixed_width_parens() -> None:
    assert repair_numbering("（ 2 ）") == "（2）"
    assert repair_numbering("(2）") == "（2）"
    assert repair_numbering("（2)") == "（2）"


def test_half_width_at_line_start_or_after_cjk_punctuation() -> None:
    assert repair_numbering("(3) 采购人") == "（3） 采购人"
    assert repair_numbering("  (3) 采购人") == "  （3） 采购人"
    assert repair_numbering("见(4)说明") == "见（4）说明"


def test_english_half_width_is_not_converted() -> None:
    assert repair_numbering("The value (2) is fine.") == "The value (2) is fine."


def test_table_lines_are_untouched() -> None:
    text = "| 2（） | x |\n|---|---|\n"

    assert repair_numbering(text) == text


def test_repair_is_idempotent() -> None:
    once = repair_numbering("2（）\n（ 3 ）\n(4) 条款\n")

    assert repair_numbering(once) == once


def test_malformed_count() -> None:
    text = "2（）\n（ 3 ）\n（4）\n(5) 条款\nTotal (2) is fine.\n"

    assert malformed_numbering_count(text) == 3


def test_number_separated_from_answer_parentheses() -> None:
    assert repair_numbering("方法？ 1 答：（）最低投标价法") == "方法？ 答：（1）最低投标价法"
    assert repair_numbering("1 答：() 内容") == "答：（1） 内容"


def test_needs_position_probe_detects_number_order() -> None:
    from dataagent.pipeline.quality.numbering import needs_position_probe

    assert needs_position_probe("以致发生公共安全责任事件，造 21 8 成 名参赛选手死亡")
    assert needs_position_probe("方法？ 1 答：（）最低投标价法")
    assert not needs_position_probe("正常的段落文字，没有数字错序。")
