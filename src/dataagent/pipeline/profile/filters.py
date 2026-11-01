"""`scope.page_filter` 字段白名单（B4）。

与 RulePack 共用同一套受限表达式引擎（B8 实现）；此处在写入前提供静态校验：
- 白名单仅限 PageProfile 的 `class` / `layout` / `signals` 三组；
- **禁止引用 `extract_quality`**（因果循环：质量分是解析后的产物，
  若允许它决定哪些页被处理，未处理的页永远没机会被判定为需要处理）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

PAGE_FILTER_ALLOWED_GROUPS: Final[frozenset[str]] = frozenset(
    {"class", "layout", "signals"}
)

# 解析后才存在的字段（quality 组整体 + 具体字段名），page_filter 一律不得引用。
_FORBIDDEN_FIELDS: Final[frozenset[str]] = frozenset(
    {"extract_quality", "quality", "tier"}
)


def validate_page_filter_fields(fields: Iterable[str]) -> None:
    """校验 page_filter 引用的字段：未知分组或解析后字段一律拒绝。"""
    for field in fields:
        parts = field.split(".")
        forbidden = _FORBIDDEN_FIELDS.intersection(parts)
        if forbidden:
            raise ValueError(
                f"page_filter 不得引用解析后字段 {sorted(forbidden)}（因果循环）：{field!r}"
            )
        if parts[0] not in PAGE_FILTER_ALLOWED_GROUPS:
            raise ValueError(
                f"page_filter 字段分组必须是 {sorted(PAGE_FILTER_ALLOWED_GROUPS)}：{field!r}"
            )
