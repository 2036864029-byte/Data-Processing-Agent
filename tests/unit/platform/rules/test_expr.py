"""受限表达式引擎测试（T116，B8）——FR-014 / taskspec 校验规则 2–3。

JSONLogic 结构表达式；白名单字段 / 函数；沙箱求值无代码执行；
`page_filter` / `skip_pages` 与 RulePack 同一引擎；禁 `extract_quality`（因果循环）。
"""

import pytest

from dataagent.platform.rules.expr import (
    PAGE_FILTER_WHITELIST,
    ExpressionError,
    FieldWhitelist,
    RestrictedExpressionEngine,
)

ENGINE = RestrictedExpressionEngine()


def evaluate(expression, data=None):
    return ENGINE.evaluate(expression, data or {})


def test_comparisons_and_boolean_logic() -> None:
    expression = {
        "and": [
            {"==": [{"var": "has_table"}, True]},
            {"==": [{"var": "pdf_type"}, "text_based"]},
        ]
    }

    assert evaluate(expression, {"has_table": True, "pdf_type": "text_based"}) is True
    assert evaluate(expression, {"has_table": False, "pdf_type": "text_based"}) is False


def test_arithmetic_and_membership() -> None:
    assert evaluate({">": [{"+": [{"var": "a"}, {"var": "b"}]}, 10]}, {"a": 6, "b": 5})
    assert evaluate({"in": [{"var": "kind"}, ["pdf", "csv"]]}, {"kind": "csv"})


def test_missing_variable_returns_none_or_default() -> None:
    assert evaluate({"var": "nope"}) is None
    assert evaluate({"var": ["nope", 3]}) == 3


def test_unknown_operator_is_rejected() -> None:
    with pytest.raises(ExpressionError):
        ENGINE.validate({"eval": ["1 + 1"]})


def test_multi_key_object_is_rejected() -> None:
    with pytest.raises(ExpressionError):
        ENGINE.validate({"and": [True, True], "or": [True, False]})


def test_string_expression_is_rejected() -> None:
    """字符串表达式属 P1 编译链路；P0 只接受结构化的 JSONLogic。"""
    with pytest.raises(ExpressionError):
        ENGINE.validate("has_table == true")


def test_depth_limit_blocks_pathological_expressions() -> None:
    engine = RestrictedExpressionEngine(max_depth=4)
    expression: object = True
    for _ in range(6):
        expression = {"!": expression}

    with pytest.raises(ExpressionError):
        engine.validate(expression)


def test_page_filter_whitelist_allows_profile_groups() -> None:
    expression = {
        "and": [
            {"==": [{"var": "layout.has_table"}, True]},
            {"==": [{"var": "class.pdf_type"}, "text_based"]},
            {"!=": [{"var": "signals.char_density"}, 0]},
        ]
    }
    engine = RestrictedExpressionEngine(whitelist=PAGE_FILTER_WHITELIST)
    data = {
        "layout": {"has_table": True},
        "class": {"pdf_type": "text_based"},
        "signals": {"char_density": 5.0},
    }

    assert engine.evaluate(expression, data) is True


@pytest.mark.parametrize(
    "path", ["extract_quality", "quality.extract_quality", "quality.tier"]
)
def test_page_filter_rejects_post_extraction_fields(path: str) -> None:
    engine = RestrictedExpressionEngine(whitelist=PAGE_FILTER_WHITELIST)

    with pytest.raises(ExpressionError):
        engine.validate({"==": [{"var": path}, 1]})


def test_page_filter_rejects_unknown_group() -> None:
    engine = RestrictedExpressionEngine(whitelist=PAGE_FILTER_WHITELIST)

    with pytest.raises(ExpressionError):
        engine.validate({"==": [{"var": "page.number"}, 1]})


def test_rules_referencing_declared_fields_are_allowed() -> None:
    whitelist = FieldWhitelist(allowed_groups=None, allowed_paths={"revenue", "cost"})
    engine = RestrictedExpressionEngine(whitelist=whitelist)

    engine.validate({"==": [{"var": "revenue"}, {"var": "cost"}]})
