# B9 黄金集工具链

样本与标注属**本地私有**，不进入仓库；所有输出写入 `var/golden/`（已在 `.gitignore`）。

## 工作流

```bash
# A. 构建 200 页清单（六类分层；真实语料不足时用构造样本补齐）
.venv/Scripts/python.exe tests/eval/build_manifest.py

# 标注准备：生成 200 页模板 + 60 页人工子集（六类各 10 页：40 拟合 / 20 留出）
.venv/Scripts/python.exe tests/eval/annotations_template.py

# B. 采集每页代理指标（待端到端管线接通后替换为系统 L1 指标）
.venv/Scripts/python.exe tests/eval/collect_metrics.py

# D. 无人工标注时输出 insufficient_labels；完成打分后回填 annotations.csv 再跑
.venv/Scripts/python.exe tests/eval/calibrate.py

# 构造样本自动评测（CER / 阅读顺序 / 表格 cell F1）
.venv/Scripts/python.exe tests/eval/evaluate.py
```

## 人工打分约定

- 四维各 0–3 分：`dim_text`（文本还原）、`dim_structure`（结构还原）、
  `dim_semantic`（语义还原）、`dim_layout`（版式还原）；
- `needs_manual`：这页是否需要人工介入（是/否），用于 `extract_quality` 标定；
- `label_source`：人工填 `human`；自动扩展标签填 `auto`，并在报告中只用人面子集验收；
- 参照物是原始 PDF 页面与系统产物并排对照，不是另一次解析结果。

## 输出

| 文件 | 说明 |
| --- | --- |
| `manifest.json` / `manifest.csv` | 200 页抽样清单（含类别/来源/页码） |
| `manifest_summary.json` | 六类可用量、选中量、构造补齐量、缺口 |
| `annotations.csv` | 全量标注模板（已存在则不覆盖） |
| `human_sheet.csv` | 60 页人工打分子集 |
| `metrics.csv` | 每页代理指标（标定输入） |
| `calibration_report.json` | 权重、阈值、留出集 Spearman / 一致率 |
| `eval_report.json` | 构造样本自动评测与门禁 |
| `viewer.html` | 样本清单查看页 |
