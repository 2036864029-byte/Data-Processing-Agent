# ADR-0005：扫描件隐藏文本层复用与 PDF 适配器质量增强

- **状态**：Accepted
- **日期**：2026-11-01
- **相关**：B3（PDF 适配器）、B4（页面画像）、B5（质检）、A8（档位策略）、
  tasks-p0-b.md Phase 12（T130–T139）

## 背景

对本地语料实测发现 pdf-inspector 1.25.2 的三个行为，直接影响扫描件与标题质量：

1. **`detect_pdf` 不填充 layout**：`pages_with_tables` / `pages_with_columns` /
   `is_complex_layout` / `has_encoding_issues` 均为空或 False；layout 只能在抽取后获得。
2. **`extract_pages_markdown` 主动丢弃页**：任一页出现 1 个 `U+FFFD` 即标记
   `suspected_garbled_text` 并返回空 Markdown；对 `invisible_text_layer` 页同样返回空。
   实测 774 页混合书有 278 页因此被清空。
3. **扫描书可能已有隐藏 OCR 文本层**：574 页扫描书在 pdf-inspector 中只返回
   `[Image: Im0]` 占位，`extract_pages_markdown` / `process_pdf` 均为空；
   但 PDFium 可逐页读出 122–1871 字（全书仅 42 个 `U+FFFD`），WPS 亦可复制文字。
4. **`process_pdf` 标题更强但不可按页使用**：同一 774 页书 `process_pdf` 识别
   199 个标题、`extract_pages_markdown` 仅 105 个；`process_pdf` 的 `pages` 为
   1-indexed，多页输出为单一拼接 Markdown（无页分隔符），逐页调用会退化为
   extract 层级且放大文件打开次数。

## 决策

### 1. 隐藏文本层探测与复用（R1）

- 使用 PDFium（`pypdfium2`）逐页读取隐藏文本层；
- 质量门：字符数 ≥ 50、`U+FFFD` 占比 ≤ 1%、可打印率 ≥ 95%；
- 达标：`text_layer=invisible_recovered`、`route=native`、`route_reason` 追加
  `text_layer_recovered`、产物 `warnings` 记录来源，**不送 OCR**；
- 不达标：保持 `needs_ocr`，进入 T3 / 聚合包 / 人工。

### 2. 画像与档位接入（R2）

- 抽取后回填 layout（表格 / 分栏 / 旋转 / 复杂度）到 PageProfile 质量段；
- `RuleTierPolicy` 增加例外：文本层可用时不再因 `pdf_type=image_based` 强制 T3；
- L1 的字符密度 / 字形覆盖输入改用恢复文本的统计口径。

### 3. 按需 `process_pdf` 标题标定（R3）

- 仅在“乱码页多 / 标题数异常”时触发一次整本 `process_pdf`；
- 标题行按“规范化文本 + 页顺序”回填到逐页产物；
- 失败标记 `heading_calibration_failed`，退回纯 extract，不阻塞交付；
- 禁止逐页调用 `process_pdf`（无文档级收益且违反文件打开预算）。

## 后果

- 新增运行时依赖 `pypdfium2`（wheel 自带 PDFium）；文本层探测按文档一次读取，
  不改变批级文件打开次数预算。
- 页面画像新增 `text_layer` 信息（JSONB，无需数据库迁移）；产物的 `warnings`
  记录恢复来源，报告可追溯。
- 574 页扫描书从“全量 OCR”改为“文本层复用 + T1 编号重建”，成本大幅下降；
  隐藏层没有标题层级，仍依赖 T1 中文编号规则重建。
- `process_pdf` 第二遍只对病态文档触发；正常招标文档保持单遍解析。
- 质量门阈值、标题→页匹配准确率进入黄金集标定（research R7 / R8）。

## 替代方案

- **直接全量 OCR**：拒绝。隐藏层已可用，574 页 × 100–300ms 浪费严重。
- **只使用 pdf-inspector**：拒绝。它忽略不可见文本，拿不到隐藏层。
- **逐页 `process_pdf`**：拒绝。标题退化为 extract 层级，且每页重新打开 PDF。
- **`process_pdf` 作为唯一产物**：拒绝。无页边界、全本常驻内存，违反批级处理约束。

## 影响范围

不改五层架构、状态机、调度器、存储抽象与插件契约；改动集中在
`platform/plugins/adapters/`、`pipeline/profile/`、`pipeline/route/`、
`kernel/policies/rule_tier.py`、`pipeline/quality/` 与 `tests/eval/`。
