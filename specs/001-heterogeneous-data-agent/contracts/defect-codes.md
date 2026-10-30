# Contract: 缺陷码封闭集（驱动修复的唯一入口）

**来源**: PRD 4.5.7 | **Plan**: plan.md | **用途**: 缺陷的封闭标签集，每条必须可定位、映射到写死的算子

## 缺陷码表（11 项）

| # | 缺陷码 | 判定条件 | `location` 必填 | 默认算子 | 档位 |
| -- | ---- | ---- | ---- | ---- | ---- |
| 1 | `encoding_loss` | `glyph_coverage < 0.95` 或 `cid_unmapped_ratio > 0.01` | `page_no`（可带 `bbox` 限定区域） | `ocr_page` | T3 |
| 2 | `low_density` | `char_density < 页型阈值` | `page_no` | `ocr_page` / `extract_region` | T3 / T2 |
| 3 | `region_empty` | 某文本块面积 > 5% 页面且字符数 ≈ 0 | `page_no` + `bbox` | `extract_region`（定点补抽） | T2 |
| 4 | `table_structure_broken` | 表格行数方差 > 0、列数不一致，或 `md` 有表但 `cells` 为空 | `page_no` + `table_index` + `bbox` | `rebuild_table_cells` | T1 |
| 5 | `table_cross_page_break` | 相邻页表头列名相似度 > 0.8 且表体触页边界 | `page_no`（起始页）+ `table_index` | `merge_cross_page_table` | T1 |
| 6 | `reading_order_broken` | `reading_order_score < 0.88` | `page_no` + `item_range` | `reorder_blocks` | T1 |
| 7 | `column_mixed` | `columns > 1` 且同一行内 bbox 跨栏 | `page_no` + `bbox` | `resegment_columns` | T2 |
| 8 | `header_footer_leak` | `header_footer_ratio > 0.05` | `page_no` + `bbox` | `strip_header_footer` | T1 |
| 9 | `page_rotated` | `/Rotate` 非 0，或文本基线倾角 > 2° | `page_no` + `angle` | `rotate_page` / `deskew` | T2 / T3 |
| 10 | `page_number_broken` | 页码缺失或序列不连续 | `page_no` | **无**（仅文档级标记） | — |
| 11 | `plugin_reason_unknown` | 上游 `ocr_reason` 未出现在映射表中 | `page_no` + `raw_reason`（原样保留） | **无**（记录 + 计数，交人工） | — |

## 四条硬性要求

1. **封闭集**：不得由模型自由生成；新增码必须走版本升级。
2. **码 → 算子映射写死在代码里**（`repair/mapping.py`），不允许运行时推断——"同一缺陷两次修复走了不同算子"不可能发生。
3. **每条缺陷必须带 `location` 且可操作**；缺 `location` 不得进入修复队列，只能统计。
4. **第 10 项不设修复算子**：页码不连续通常是原文档问题（装订、扫描缺页），修复它等于篡改证据；文档级标记 + 报告说明是唯一正确处理。

## 两张映射表（可变性相反，不得混为一谈）

| 映射 | 性质 | 存放 | 可变性 |
| ---- | ---- | ---- | ---- |
| `reason → defect_code`（上游开放集 → 本系统封闭集） | **配置**，可随插件版本变更 | 配置文件，按插件版本分组 | 可配（输入集合不由我们控制） |
| `defect_code → operator`（本系统封闭集 → 本系统算子） | **代码**，写死 | `repair/mapping.py` | 固定（输出行为必须可复现） |

## 上游原因码接入规则

1. 映射表放配置，不放代码（禁止 `if reason == "..."` 字面量）。
2. 未知 reason 一律落第 11 项 `plugin_reason_unknown`，`raw_reason` 原样记录；不得丢弃、不得凭猜测归入邻近码。
3. 映射表按插件版本分组——上游升级新增 reason 自动落入未知桶，报告能看出"这是升级带来的"。
4. `plugin_reason_unknown` 计数进运营指标；单次任务占比 > 5% 告警（大概率插件升级或映射表过期）。
5. 映射只做一次、单向：`defect_code` 不反推 `reason`；报告里两个字段并列（上游证据 + 本系统判定）。
