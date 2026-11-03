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
