# Contract: 质检器（QC）输出契约

**来源**: PRD 4.4.8 | **Plan**: plan.md | **用途**: L1/L2/L3 质检结果的结构化输出；`defects[]` 是核心，`verdict` 只是结论

## Schema（JSON）

```json
{
  "verdict": "pass | fail | uncertain",
  "extract_quality": 0.62,
  "fidelity": 0.93,
  "defects": [
    {
      "code": "table_row_inconsistent",
      "location": {"type": "table", "index": 2, "bbox": [120, 340, 520, 610]},
      "evidence": {"row_counts": [5, 5, 3, 5], "variance": 1.2},
      "severity": 0.8,
      "suggested_action": "rebuild_table_cells"
    },
    {
      "code": "encoding_loss",
      "location": {"type": "page", "page_no": 82},
      "evidence": {"glyph_coverage": 0.31, "unmapped": 1240},
      "severity": 0.95,
      "suggested_action": "ocr_page"
    }
  ],
  "attribution": "table_structure",
  "recommended_action": "resegment_table",
  "qc_confidence": 0.88
}
```

## 硬性要求

1. 必须是结构化字段，禁止自由文本；QC 只读，不得修改产物。
2. `defects[]` 是核心：只有分数没有缺陷清单 = 知道这页不好但不知道哪儿不好 → 只能整页重做（对确定性函数无意义）。
3. 每条缺陷必须带 `location`，精确到可操作粒度（表格序号 + bbox / 页码 + 区域）；**无 `location` 视为不可修复、拒绝入库**。
4. `defects[].code` 取自封闭集（见 defect-codes.md），不得自造。
5. `qc_confidence` 是 **QC 对自身判定的置信度**，与被评估页面的 `extract_quality` 不是同一个东西，不得混用。

## 指标口径（L1 逐页，全量）

| 指标 | 定义 |
| ---- | ---- |
| `char_density` | 抽取字符数 / 文本区域面积 |
| `glyph_coverage` | `(cmap_gaps.codes - cmap_gaps.unmapped) / cmap_gaps.codes` |
| `cid_unmapped_ratio` | 未映射 CID 占比 |
| `empty_region_ratio` | 空白块面积 / 页面面积 |
| `table_cell_fill` | 非空单元格 / 总单元格（仅 T1 补齐 cell 后） |
| `row_col_consistency` | 每行列数方差 |
| `reading_order_score` | y 单调性 + 列切换惩罚 |
| `bbox_coverage` | 有坐标文本 / 总文本 |
| `header_footer_leak` | 页眉页脚进入正文比例 |
| `page_number_ok` | 页码存在且连续 |

`extract_quality` = 上表加权合成（不是第 11 个指标），权重由黄金集拟合。

**硬门控**（任一不过 → fail）：`glyph_coverage > 0.95`、`cid_unmapped_ratio < 0.01`、`empty_region_ratio < 0.30`、`table_cell_fill > 0.90`（仅表格页且 T1 后）、`page_number_ok == true`、`char_density > 阈值（按页型分档）`。

## QC 自身质量要求

- 黄金集评测：precision ≥ 0.95、recall ≥ 0.90；否则"它说 pass"不可信。
- `extract_quality` 与人工"需介入"判定一致率 ≥ 0.85（单独标定，决定 T1/T2 触发是否准确）。
