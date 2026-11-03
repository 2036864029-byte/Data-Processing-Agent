"""CJK 文本归一回流测试：伪空格、硬换行、编号/表格边界与幂等。"""

from __future__ import annotations

from dataagent.pipeline.quality.text_normalize import normalize_cjk_text


def test_removes_phantom_spaces_between_cjk() -> None:
    assert normalize_cjk_text("依 法审核管理。") == "依法审核管理。"


def test_merges_hard_wrapped_lines_without_terminal_punctuation() -> None:
    text = "第一段没有句号\n继续内容。\n第二段结束。\n"

    assert normalize_cjk_text(text) == "第一段没有句号继续内容。\n第二段结束。\n"


def test_numbering_starts_new_paragraph_and_merges_continuation() -> None:
    text = "前文没有句号\n第二十八条 采购人应当\n提交材料。\n"

    assert normalize_cjk_text(text) == (
        "前文没有句号\n第二十八条 采购人应当提交材料。\n"
    )


def test_lists_headings_and_tables_are_preserved() -> None:
    text = "# 标题\n- 项目一\n- 项目二\n| a | b |\n|---|---|\n| 1 | 2 |\n"

    assert normalize_cjk_text(text) == text


def test_table_block_is_not_reflowed_but_following_text_is() -> None:
    text = "| a | b |\n|---|---|\n| 1 | 2 |\n正文\n继续。\n"

    assert normalize_cjk_text(text) == (
        "| a | b |\n|---|---|\n| 1 | 2 |\n正文继续。\n"
    )


def test_latin_lines_join_with_space() -> None:
    assert normalize_cjk_text("This is a line\nthat continues.") == (
        "This is a line that continues."
    )


def test_cjk_latin_boundary_keeps_space() -> None:
    assert normalize_cjk_text("中文 text\n继续") == "中文 text 继续"


def test_blank_lines_are_preserved() -> None:
    text = "第一段。\n\n第二段。\n"

    assert normalize_cjk_text(text) == text


def test_normalization_is_idempotent() -> None:
    once = normalize_cjk_text("依 法\n审核。\n\n第二段。\n")

    assert normalize_cjk_text(once) == once


def test_empty_text_is_unchanged() -> None:
    assert normalize_cjk_text("") == ""
    assert normalize_cjk_text("   \n") == "   \n"
