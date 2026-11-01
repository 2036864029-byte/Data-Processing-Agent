"""`scope.page_filter` 字段白名单（B4）——实现委托 platform.rules.expr（B8 同源）。

白名单仅限 PageProfile 的 `class` / `layout` / `signals` 三组；
**禁止引用 `extract_quality`**（因果循环）。与 RulePack 共用同一套受限表达式引擎。
"""

from dataagent.platform.rules.expr import (
    PAGE_FILTER_ALLOWED_GROUPS,
    validate_page_filter_fields,
)

__all__ = ["PAGE_FILTER_ALLOWED_GROUPS", "validate_page_filter_fields"]
