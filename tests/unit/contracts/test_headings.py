"""标题编号契约测试（Phase 1）：中文编号分类 + 角色层级映射。

标题层级规则只有一处定义：`contracts/headings.py`。pipeline（建块 / 结构视图 /
版面修复）与 platform（标题标定）都消费这里的结论，不得各自实现一套正则。
"""

import pytest

from dataagent.contracts.headings import (
    HeadingSignal,
    NumberingKind,
    classify_numbering,
    level_from_role,
    numbering_level,
)


@pytest.mark.parametrize(
    ("text", "kind", "level"),
    [
        ("第一章 总则", NumberingKind.CHAPTER, 1),
        ("第3编 刑事", NumberingKind.CHAPTER, 1),
        ("第四部分 附则", NumberingKind.CHAPTER, 1),
        ("第二节 管辖", NumberingKind.SECTION, 2),
        ("一、总则", NumberingKind.TOP_SECTION, 2),
        ("十三、附则", NumberingKind.TOP_SECTION, 2),
        ("（一）立案", NumberingKind.CLAUSE, 3),
        ("1. 总则", NumberingKind.ENUMERATION, 4),
        ("（1）总则", NumberingKind.ENUMERATION, 4),
    ],
)
def test_classify_numbering_levels(
    text: str, kind: NumberingKind, level: int
) -> None:
    signal = classify_numbering(text)
    assert signal == HeadingSignal(kind=kind, level=level)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "第一条规定了本罪的构成要件。",
        "一是要加强管理。",
        "（一）",
        "一、",
        "正文内容没有任何编号",
        "2.5 亿元的经营收入",
    ],
)
def test_non_heading_text_is_not_classified(text: str) -> None:
    assert classify_numbering(text) is None


def test_numbering_level_matches_default_mapping() -> None:
    assert numbering_level(NumberingKind.CHAPTER) == 1
    assert numbering_level(NumberingKind.SECTION) == 2
    assert numbering_level(NumberingKind.TOP_SECTION) == 2
    assert numbering_level(NumberingKind.CLAUSE) == 3
    assert numbering_level(NumberingKind.ENUMERATION) == 4


@pytest.mark.parametrize(("role", "level"), [("H1", 1), ("H3", 3), ("H6", 6)])
def test_level_from_structure_role(role: str, level: int) -> None:
    assert level_from_role(role) == level


@pytest.mark.parametrize("role", ["P", "Span", "H7", "", "h1", "H"])
def test_non_heading_role_has_no_level(role: str) -> None:
    assert level_from_role(role) is None


def test_promotable_kinds_are_structural_only() -> None:
    """可独立成为标题的只有章/节/一、；枚举项与列表编号只提供层级。"""
    assert NumberingKind.CHAPTER.promotable
    assert NumberingKind.SECTION.promotable
    assert NumberingKind.TOP_SECTION.promotable
    assert not NumberingKind.CLAUSE.promotable
    assert not NumberingKind.ENUMERATION.promotable
