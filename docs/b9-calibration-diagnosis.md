# B9 标定诊断（2026-11-06）

## 结论

60 页人工四维打分**已完成**（T122 可关闭）；T123 / T124 未达标的瓶颈不是标注页数，
而是三件事：

1. 生产 `extract_quality` 仍用 P0 手写权重，标定脚本拟合出的权重没有接进 L1；
2. 60 页里只有 **7 个** `needs_manual=是` 正例，QC precision/recall 与阈值工作点不稳定；
3. `fidelity` 的 PRD 定义（CER / TEDS / semantic_match / layout_iou）需要真值，
   而真实页（54/60）没有真值，当前只能用代理特征。

## 数据现状

- 200 页黄金集：六类各 33/33/33/33/34/34；
- 人工子集 60 页（六类各 10）：`annotations.csv` 四维齐全，`label_source=human`；
- 标签分布：`needs_manual` 是 7 / 否 53；人工总分（0–12）分布 0（2）、6（12）、
  8–11（36）、12（2）；
- 7 个正例：2 个纯扫描空产物（R-0139/R-0140）、3 个单表（R-0057/R-0060/R-0063）、
  2 个跨页表（R-0073/R-0074）；其中 R-0073 的 `table_cell_fill=0.988`，
  单看表格填充率无法识别。

## 当前指标

| 指标 | 结果 | 门槛 |
| ---- | ---- | ---- |
| 生产 `extract_quality` vs 人工总分 Spearman | **0.155** | > 0.80 |
| 旧代理标定（7 特征）留出 Spearman | 0.720 | > 0.80 |
| 旧代理标定 5 折 CV Spearman | 0.714 ± 0.065 | > 0.80 |
| 7 特征全量拟合的样本内上限 | 0.736 | — |
| 加入 text_layer 特征后样本内上限 | 0.821（CV 0.68，过拟合） | — |
| 最优稳健代理子集（clean_spacing / prose / numbering / recovered） | 重复 CV 0.79 ± 0.16；留出 0.786；按类留一 4/6 类为负 | > 0.80 |
| 生产 verdict vs needs_manual | P 0.333 / R 0.286 | ≥0.95 / ≥0.90 |
| 标定阈值 0.36 作用在生产质量分 | P 1.000 / R 0.286 | ≥0.95 / ≥0.90 |

生产 verdict 只覆盖 2/7 正例（两个纯扫描页），5 个表格/跨页表正例全部漏判；
同时把 3 个多栏页、1 个编码页误判为 fail。

复现：`uv run python tests/eval/qc_gate_report.py`（输出 `var/golden/qc_gate_report.json`）。

## A 路径第 1 步已落地（2026-11-06）

- 文本质量特征抽到 `pipeline/quality/text_metrics.py`，生产 L1 与标定同源；
- `extract_quality` 纳入 clean_spacing / prose_ratio / numbering_ratio 与
  table_cell_fill（非表格页中性 1.0），权重由 60 页全量拟合；
- 标定脚本改为生产分量口径，输出全量权重与工作点阈值 0.43。

更新后实测：

| 指标 | 改造前 | 改造后 |
| ---- | ---- | ---- |
| 生产质量分 vs 人工总分 Spearman | 0.155 | 0.488（CV 0.598） |
| needs_manual 一致率 | — | 0.917 |
| QC precision / recall（阈值 0.43） | 0.333 / 0.286 | 0.667 / 0.286 |
| verdict precision / recall | 0.333 / 0.286 | 不变 |

仍缺：5 个表格/跨页表正例的分数高于阈值（表头/合并单元格/跨页续接等结构问题
没有被现有指标捕获），需要更细的表格结构特征与更多带 `error_notes` 的正例标注。


## 根因

1. **权重未接线**：`L1Checker._extract_quality` 使用 `_QUALITY_WEIGHTS` 常量
   （glyph 0.30 / cid 0.20 / char_density 0.20 / empty 0.15 / reading_order 0.10 /
   bbox 0.05），P0 多数指标为 None，实际只剩 glyph + char_density；
   表格指标与文本质量特征未参与合成，拟合结果也未注入 L1。
2. **正例过少**：7 个正例不足以稳定阈值与 P/R；同一人工总分 6 的页面里
   正负混杂，`total` 与 `needs_manual` 的 Spearman 只有 0.50，人工标签内部一致性有限。
3. **fidelity 缺真值**：真实页没有转录/表格真值；PDFium 参考 CER 只覆盖 40/60
   （扫描与编码页无参考），基于 CER 的组合样本内仅 0.758。
4. **口径错位**：人工打分看的是查看器展示产物（空产物页用 PDFium 恢复文本），
   而 `collect_metrics.py` 的 `char_count/glyph_coverage` 取自 pdf-inspector 原始文本项；
   扫描页会出现“md 1313 字、指标 char_count=12”的错位。表格指标与文本质量特征
   取自展示产物，二者来源不一致。

## 可选路径

**A. 真正达标（推荐）**
改造生产 L1：把 `clean_spacing / prose_ratio / numbering_ratio / table_cell_fill /
table_header_ok / text_layer` 纳入 `L1Metrics` 与 `extract_quality`，权重由黄金集拟合并
成为默认常量；同时扩充人工标注（重点补 `needs_manual=是` 正例、填 `error_notes`、
统一评分口径），并为真实页建立参考真值（人工转录或 PDFium/pdfplumber 参考）。
预计需要：生产改造 + 60–120 页增量标注 + 真值方案确认。

**B. 调整验收口径**
`fidelity` 以重复 5 折 CV 均值评估（代理模型约 0.79–0.82），QC P/R 作为报告项而非硬门禁；
可快速关闭 T123/T124，但验收说服力弱，PRD 的“质量可证明”承诺打折。

**C. 暂缓**
T122 关闭；T123/T124 保持未勾并记录本诊断，转做 P1/收尾项，待真值或增量标注到位再启动。

## 数据质量问题（已确认，不影响现有合并）

- `annotations_filled.csv` 的 R-0096 缺 `dim_layout`，但 `annotations.csv` 完整；
  `import_annotations.py` 会跳过四维不全的行，不会覆盖完整值。
- 构造样本在 `manifest.json` 中 `doc_id` 为空；本次评测脚本按运行结果回填。
- 6 个构造跨页表样本内容与打分完全相同（C-0082/0088/0090/0095/0111/0114），
  属重复样本，会放大该类权重。
