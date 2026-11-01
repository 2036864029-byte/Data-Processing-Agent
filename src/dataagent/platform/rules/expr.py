"""受限表达式引擎（B8/T118 / FR-014 / research「受限表达式」）。

JSONLogic 结构表达式 + 白名单算子 + 白名单字段，解释执行、**无代码执行**。
`page_filter` / `skip_pages` 与 RulePack `checks[]` 共用本引擎。

说明：`json-logic==0.6.3` 在 Python 3.12 下不可用（`dict_keys` 下标错误），
P0 内置同语法的受限求值器；字符串表达式属 P1 编译链路（NL→RulePack），
P0 仅接受结构化 JSONLogic。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

PAGE_FILTER_ALLOWED_GROUPS: Final[frozenset[str]] = frozenset(
    {"class", "layout", "signals"}
)
PAGE_FILTER_FORBIDDEN_NAMES: Final[frozenset[str]] = frozenset(
    {"extract_quality", "quality", "tier"}
)

ALLOWED_OPERATORS: Final[frozenset[str]] = frozenset(
    {
        "var",
        "==",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "and",
        "or",
        "!",
        "!!",
        "if",
        "?:",
        "in",
        "cat",
        "+",
        "-",
        "*",
        "/",
        "%",
        "min",
        "max",
    }
)

_ARITHMETIC = frozenset({"+", "-", "*", "/", "%"})


class ExpressionError(ValueError):
    """表达式不合法（非白名单算子 / 字段 / 结构），拒绝求值。"""


@dataclass(frozen=True, slots=True)
class FieldWhitelist:
    """字段白名单。

    - `allowed_groups`：仅允许这些顶层分组（page_filter 用 class/layout/signals）；
    - `allowed_paths`：仅允许这些完整路径（RulePack 用已声明字段）；
    - `forbidden_names`：任何分段命中即拒绝（解析后字段，因果循环）。
    """

    allowed_groups: frozenset[str] | None = None
    allowed_paths: frozenset[str] | None = None
    forbidden_names: frozenset[str] = frozenset()

    def validate(self, path: str) -> None:
        parts = path.split(".")
        forbidden = self.forbidden_names.intersection(parts)
        if forbidden:
            raise ExpressionError(
                f"表达式不得引用解析后字段 {sorted(forbidden)}（因果循环）：{path!r}"
            )
        if self.allowed_groups is not None and parts[0] not in self.allowed_groups:
            raise ExpressionError(
                f"字段分组必须是 {sorted(self.allowed_groups)}：{path!r}"
            )
        if self.allowed_paths is not None and path not in self.allowed_paths:
            raise ExpressionError(f"字段未声明，不得引用：{path!r}")


PAGE_FILTER_WHITELIST: Final[FieldWhitelist] = FieldWhitelist(
    allowed_groups=PAGE_FILTER_ALLOWED_GROUPS,
    forbidden_names=PAGE_FILTER_FORBIDDEN_NAMES,
)


def validate_page_filter_fields(fields: Iterable[str]) -> None:
    """校验 page_filter 引用的字段（B4 同源实现，pipeline 层委托到这里）。"""
    for field in fields:
        PAGE_FILTER_WHITELIST.validate(field)


class RestrictedExpressionEngine:
    def __init__(
        self,
        *,
        whitelist: FieldWhitelist | None = None,
        allowed_operators: frozenset[str] = ALLOWED_OPERATORS,
        max_depth: int = 32,
        max_nodes: int = 512,
    ) -> None:
        self._whitelist = whitelist
        self._allowed = allowed_operators
        self._max_depth = max_depth
        self._max_nodes = max_nodes

    def validate(self, expression: Any) -> None:
        if isinstance(expression, str):
            raise ExpressionError(
                "字符串表达式属 P1 编译链路；P0 只接受结构化 JSONLogic"
            )
        self._validate(expression, depth=0, counter=[0])

    def evaluate(self, expression: Any, data: Mapping[str, Any] | None = None) -> Any:
        self.validate(expression)
        return self._evaluate(expression, data or {})

    def _validate(self, node: Any, *, depth: int, counter: list[int]) -> None:
        counter[0] += 1
        if counter[0] > self._max_nodes:
            raise ExpressionError(f"表达式节点数超过上限 {self._max_nodes}")
        if depth > self._max_depth:
            raise ExpressionError(f"表达式嵌套超过上限 {self._max_depth}")

        if isinstance(node, Mapping):
            if len(node) != 1:
                raise ExpressionError("JSONLogic 操作必须有且仅有一个键")
            operator, arguments = next(iter(node.items()))
            if operator not in self._allowed:
                raise ExpressionError(f"算子不在白名单：{operator!r}")
            if operator == "var":
                self._validate_var(arguments)
            for argument in _as_list(arguments):
                self._validate(argument, depth=depth + 1, counter=counter)
            return
        if isinstance(node, list):
            for item in node:
                self._validate(item, depth=depth + 1, counter=counter)
            return
        if not isinstance(node, (str, int, float, bool, type(None))):
            raise ExpressionError(f"表达式节点类型不合法：{type(node).__name__}")

    def _validate_var(self, arguments: Any) -> None:
        path = arguments if isinstance(arguments, str) else arguments[0]
        if not isinstance(path, str):
            raise ExpressionError("var 的字段名必须是字符串")
        if self._whitelist is not None:
            self._whitelist.validate(path)

    def _evaluate(self, node: Any, data: Mapping[str, Any]) -> Any:
        if isinstance(node, Mapping):
            operator, arguments = next(iter(node.items()))
            if operator == "var":
                return self._evaluate_var(arguments, data)
            if operator == "and":
                values = [self._evaluate(item, data) for item in _as_list(arguments)]
                for value in values:
                    if not _truthy(value):
                        return value
                return values[-1] if values else True
            if operator == "or":
                values = [self._evaluate(item, data) for item in _as_list(arguments)]
                for value in values:
                    if _truthy(value):
                        return value
                return values[-1] if values else False
            if operator in {"if", "?:"}:
                return self._evaluate_if(_as_list(arguments), data)
            values = [self._evaluate(item, data) for item in _as_list(arguments)]
            return _apply(operator, values)
        if isinstance(node, list):
            return [self._evaluate(item, data) for item in node]
        return node

    def _evaluate_var(self, arguments: Any, data: Mapping[str, Any]) -> Any:
        if isinstance(arguments, str):
            return _lookup(data, arguments)
        path = arguments[0]
        default = self._evaluate(arguments[1], data) if len(arguments) > 1 else None
        value = _lookup(data, path)
        return default if value is None else value

    def _evaluate_if(self, arguments: list[Any], data: Mapping[str, Any]) -> Any:
        index = 0
        while index + 1 < len(arguments):
            if _truthy(self._evaluate(arguments[index], data)):
                return self._evaluate(arguments[index + 1], data)
            index += 2
        return self._evaluate(arguments[index], data) if index < len(arguments) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _truthy(value: Any) -> bool:
    return bool(value)


def _lookup(data: Any, path: str) -> Any:
    if path == "":
        return data
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _apply(operator: str, values: list[Any]) -> Any:
    if operator == "==":
        return values[0] == values[1]
    if operator == "!=":
        return values[0] != values[1]
    if operator == ">":
        return values[0] > values[1]
    if operator == ">=":
        return values[0] >= values[1]
    if operator == "<":
        return values[0] < values[1]
    if operator == "<=":
        return values[0] <= values[1]
    if operator == "!":
        return not _truthy(values[0])
    if operator == "!!":
        return _truthy(values[0])
    if operator == "in":
        return values[0] in values[1]
    if operator == "cat":
        return "".join("" if value is None else str(value) for value in values)
    if operator == "min":
        return min(values)
    if operator == "max":
        return max(values)
    return _arithmetic(operator, values)


def _arithmetic(operator: str, values: list[Any]) -> Any:
    if operator not in _ARITHMETIC:
        raise ExpressionError(f"未实现的算子：{operator!r}")
    try:
        if operator == "+":
            return values[0] + values[1]
        if operator == "-":
            return values[0] - values[1]
        if operator == "*":
            return values[0] * values[1]
        if operator == "/":
            return values[0] / values[1]
        return values[0] % values[1]
    except (TypeError, ZeroDivisionError) as exc:
        raise ExpressionError(f"算术求值失败（{operator}）：{exc}") from exc
