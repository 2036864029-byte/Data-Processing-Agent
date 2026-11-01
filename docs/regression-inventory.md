# 回归必写测试盘点（T069）

宪法 / quickstart「回归验证」六个必写测试的落地状态。**单向引用**：`#2 / #3 / #4 / #6` 的
P0-B 全量盘点只在 `specs/001-heterogeneous-data-agent/tasks-p0-b.md` T126 维护，本文件不重复。

| # | 断言 | 归属模块 | P0-A 状态 | 测试位置 |
| -- | ---- | ---- | ---- | ---- |
| 1 | 页码基准：`extract_pages_markdown(path, pages=[k])` 后 `pages[0].page == k`，该页有表时 `k+1 in pages_with_tables` | `contracts/pages.py` | **已覆盖并全绿** | `tests/unit/contracts/test_pages.py` |
| 2 | 未知原因透传：映射表外 reason → `plugin_reason_unknown`，`raw_reason` 原样保留 | `pipeline/repair/` | P0-B（B6） | 见 `tasks-p0-b.md` T126 |
| 3 | 算子不可变且幂等：原产物不变、两次调用候选一致 | `pipeline/repair/` | P0-B（B6） | 见 `tasks-p0-b.md` T126 |
| 4 | 无进展停机：`changed == false` 立即放弃、不重试第二次 | `pipeline/repair/` | 页级门控已覆盖；完整回路 P0-B（B6） | `tests/unit/kernel/policies/test_page_level.py`；回路见 T126 |
| 5 | 主键内容哈希 / 幂等键：锁定版本重跑历史任务，产物哈希一致 | `contracts/ids.py` `platform/store/` | **已覆盖并全绿** | `tests/unit/contracts/test_ids.py`、`tests/integration/store/test_objectstore.py` |
| 6 | 增强回路收敛：同码唯一、三层预算耗尽后升级、无无限循环 | `pipeline/repair/` | P0-B（B6） | 见 `tasks-p0-b.md` T126 |

**P0-A 结论**：#1、#5 全绿；#2、#3、#6 的完整实现依赖 P0-B 的增强回路；#4 的页级
`changed == false` 门控已在 A8 规则实现中覆盖，回路级断言随 B6 落地。
