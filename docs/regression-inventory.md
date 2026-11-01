# 回归必写测试盘点（T069 / T126）

宪法 / quickstart「回归验证」六个必写测试的落地状态。P0-A 先盘点 #1/#5，
P0-B 在 T126 完成全量盘点；本文件为最终状态。

| # | 断言 | 归属模块 | 状态 | 测试位置 |
| -- | ---- | ---- | ---- | ---- |
| 1 | 页码基准：`extract_pages_markdown(path, pages=[k])` 后 `pages[0].page == k`，该页有表时 `k+1 in pages_with_tables` | `contracts/pages.py` | **全绿** | `tests/unit/contracts/test_pages.py` |
| 2 | 未知原因透传：映射表外 reason → `plugin_reason_unknown`，`raw_reason` 原样保留 | `pipeline/repair/reasons.py` | **全绿** | `tests/unit/pipeline/repair/test_reasons.py` |
| 3 | 算子不可变且幂等：原产物不变、两次调用候选一致 | `pipeline/repair/operators/` | **全绿** | `tests/unit/pipeline/repair/test_operators.py` |
| 4 | 无进展停机：`changed == false` 立即放弃、不重试第二次 | `pipeline/repair/loop.py` | **全绿** | `tests/unit/pipeline/repair/test_loop.py::test_changed_false_is_not_retried`；页级门控 `tests/unit/kernel/policies/test_page_level.py` |
| 5 | 主键内容哈希 / 幂等键：锁定版本重跑历史任务，产物哈希一致 | `contracts/ids.py` `platform/store/` | **全绿** | `tests/unit/contracts/test_ids.py`、`tests/integration/store/test_objectstore.py` |
| 6 | 增强回路收敛：同码唯一、三层预算耗尽后升级、无无限循环 | `pipeline/repair/loop.py` | **全绿** | `tests/unit/pipeline/repair/test_loop.py`（同码唯一 / rounds / budget / time） |

**P0-B 结论**：六个必写测试全部落地并通过（T126 定向回归 89 项全绿）；
后续任何改动必须保持这六条不回归。
