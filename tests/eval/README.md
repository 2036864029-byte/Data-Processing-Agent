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

# 生成 60 页对照查看器（左页图 + 右产物 + 打分控件）
.venv/Scripts/python.exe tests/eval/prepare_review.py
# 用浏览器打开 var/golden/review/index.html 逐页打分，完成后导出 annotations_filled.csv
.venv/Scripts/python.exe tests/eval/import_annotations.py

# D. 无人工标注时输出 insufficient_labels；完成打分后运行
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
- 查看器内置「评分标准」折叠面板与每张卡片的判据提示（结构=层级/段落/列表/表格行列/顺序；版式=块位置/分栏/bbox）。
- 空产物页会带状态：`text_layer_recovered`（PDFium 恢复隐藏/乱码文本层，展示恢复文本）、
  `needs_ocr`（纯扫描无文本层，需 OCR）或 `suspected_garbled`（旧口径回退）。

## 输出

| 文件 | 说明 |
| --- | --- |
| `manifest.json` / `manifest.csv` | 200 页抽样清单（含类别/来源/页码） |
| `manifest_summary.json` | 六类可用量、选中量、构造补齐量、缺口 |
| `manifest.csv` 的 `table_rows/cols/cells` | 表格尺寸（短表/伪表格不进入表格类；构造样本含 36×6 与 61×6 长表） |
| `manifest.csv` 的 `text_layer/text_chars/bad_ratio` | 文本层口径：present / invisible_text_layer / garbled_text_layer / pure_scan / absent；汇总见 manifest_summary |
| `annotations.csv` | 全量标注模板（已存在则不覆盖） |
| `human_sheet.csv` | 60 页人工打分子集 |
| `metrics.csv` | 每页代理指标（标定输入） |
| `review/index.html` | 60 页并排对照查看器（左页图 / 右产物 / 打分） |
| `parsed/<sample_id>.md` | 每页产物（查看器右侧内容） |
| `annotations_filled.csv` | 查看器导出的打分（导入后写回 annotations.csv） |
| `calibration_report.json` | 权重、阈值、留出集 Spearman / 一致率 |
| `eval_report.json` | 构造样本自动评测与门禁；含文本层恢复率与恢复路径 CER |
| `viewer.html` | 样本清单查看页 |
