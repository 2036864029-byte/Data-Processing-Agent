# ADR-0002：页码转换符号与唯一转换点

- **状态**：Accepted
- **日期**：2026-11-01
- **相关**：A3（T017）、`contracts/pages.py`、`scripts/check-gates.sh`

## 背景

pdf-inspector 不同接口、甚至同一返回对象内部页码基准不一致：
`extract_pages_markdown` 的 `pages[].page` 是 0-indexed，而同对象的 `pages_with_tables`
是 1-indexed。系统内部统一使用 1-indexed；若在接口级做一次转换，仍会差一页。

## 决策

- 全工程唯一页码转换点在 `contracts/pages.py`，按**字段级**转换。
- 符号固定为 `page_from_plugin`（0-indexed → 1-indexed）与 `page_to_internal`
  （1-indexed → 0-indexed），便于 CI grep 门禁匹配。
- 这两个符号只允许出现在 `contracts/pages.py`；其他文件出现即门禁失败。
- 边界语义：`page_from_plugin(0) == 1`；负数插件页码拒绝；`page_to_internal(1) == 0`；
  内部第 0 页拒绝。

## 后果

- 所有插件 / 页码边界必须经这两个函数，禁止就地 `+1/-1`。
- 门禁脚本把符号扩散视为错误，避免“第二个转换点”悄悄出现。
- 新增插件若页码基准不同，只能在 `pages.py` 内扩展字段级转换，不得改动调用方。
