# ADR-0006：P0-B 功能模块关键实现决策

- **状态**：Accepted
- **日期**：2026-11-01
- **相关**：P0-B B1–B9、Phase 12（R1–R4）、tasks-p0-b.md

## 背景

P0-B 在 A1–A9 基础层之上落地接入、格式判定、路由、画像、质检、修复、
合并交付、规则包与评测。实现过程中有若干影响可复现性与边界行为的决策，
集中记录如下。

## 决策

### B1 接入与体检

- PDF 结构探针**自建**（`pipeline/ingest/pdf_probe.py`），不依赖解析插件，
  保证“体检先于解析”；有界 zlib 解压、页树迭代遍历、64MB 扫描上限。
- `doc_id` = SHA-256 内容哈希；重复内容直接复用，不重复体检/解析/计费。

### B2 一级格式判定

- 纯规则：magic + 编码 + 分隔符统计；只读前 256KB、单文件 ≤50ms。
- 后缀只作提示；冲突记 `extension_mismatch` 事件，不按后缀判定。
- 歧义不猜：小样本双跑比较质量与成本；无法分类进待确认队列并告警。

### B3 路由与引擎接入

- 一级格式决策；二级仅 PDF 家族调用 `pdf.classify`，非 PDF 不经过分类能力。
- `engine.pdf` 双 capability：classify 用 `detect_pdf`（不产内容）；
  extract 用 `extract_pages_markdown`（逐页、批量传入）。
- 批大小 32，扫描页单独成批，文档亲和键统一；文件打开次数受预算约束。
- 隐藏文本层恢复与按需 `process_pdf` 标题标定见 **ADR-0005**。

### B4 页面画像

- 两段写入：路由段直接采用路由决策快照（决策与画像同源），质量段解析后写。
- `route_reason` 原样保留；`page_filter` 字段白名单限 `class/layout/signals`，
  禁引用 `extract_quality`（因果循环）。
- 因 `detect_pdf` 不填 layout，抽取后回填表格/分栏/旋转/复杂度。

### B5 质检

- L1 全量逐页（任何性能档位不削减）；硬门控 + 缺陷定位到页。
- L2 分层抽样：quality 15 页 / balanced 5 页 / fast fail+首末页；
  fast 禁 T3、STP 标注不可用于验收。
- 质检结果先落库再触发下游；QC 只读，不重新解析、不改产物。

### B6 T1 后处理与修复

- 算子库 9 个，P0 实做 `rebuild_table_cells` / `strip_header_footer` /
  `merge_cross_page_table`；无坐标或需重跑的算子诚实返回 unchanged。
- 增强回路：同码唯一、`changed == false` 立即停机、轮次/耗时/成本三层预算、
  候选须 ≥5% 质量提升且缺陷数减少。
- 失败页聚包 50–100 页、只抽失败页、页码映射参与幂等；
  降级默认 `mark_only`，`enhancer` 配置化。
- reason→defect_code 映射放 YAML（按插件版本分组），未知原因原样透传。

### B7 合并、交付、报告与输出

- 页码重排 + 缺口占位不跳页；跨页表按表头相似度 >0.8 + 列数一致 + 触页边界合并。
- 标题层级重建（禁跳级）；文档级校验可注入（B8 checks[] 接入点）。
- 三档交付：0 → complete、≤5% → partial_with_gaps、>5% → needs_review。
- 报告分层且按需生成：总览 + 聚合 ≤10 行 + 仅缺口文件清单 + 明细不预生成。

### B8 RulePack 与表达能力

- **`json-logic==0.6.3` 在 Python 3.12 下不可用**（内部 `dict_keys` 下标错误）；
  P0 在 `platform/rules/expr.py` 自建同语法受限求值器（白名单算子/字段、
  max_depth/max_nodes），无代码执行；字符串表达式留待 P1 编译链路。
- RulePack 每条规则必带原始需求文本；发布版本必须递增、支持历史回滚与 dry-run。
- 字段提取 P0 只做声明式锚点层；模板/模型兜底属后续阶段。

### B9 黄金集与评测

- 六类分层 200 页；真实语料不足时用构造样本补齐（长表 36×6 / 61×6）。
- 人工四维 0–3 打分（文本/结构/语义/版式）；工具链输出 manifest、标注模板、
  对照查看器、代理指标、标定与评测脚本。
- 文本层口径拆分 present / invisible_text_layer / garbled_text_layer / pure_scan。
- 人工标注与 Spearman/一致率标定仍待完成（T122–T124 未关闭）。

## 后果与已知限制

- 六个必写测试全部落地（T126）；全量测试与工程门禁保持全绿。
- **端到端编排已接线**（2026-11-02）：`pipeline/task_runner.py` 串联
  ingest→sniff→route→extract→profile→L1→repair→merge→delivery→report/output，
  worker 通过 `TaskRunnerBatchHandler` 按批调用；`scripts/e2e_p0.py` 100 份混合语料
  S1–S4 + S6 共 17 项断言全部通过。缺口页以占位替代原产物（不得同页并存）。
- **B-PLAN 已实现**（T120/T121）：`kernel/plan.py` 解析即冻结；分支只能前向跳转，
  分支目标是互斥入口（线性推进时跳过）；统计指标白名单在生成期拒绝未知字段。
- **bench 已实现**（T127）：单页 p50/p95、Python 堆峰值、文件打开预算、
  批大小 / worker 公式、OOM 降批链均有真实输出；本机实测 p50 9.6ms / p95 17.7ms。
- **仍然待办**：
  - B9 人工标注与标定（T122–T124）；
  - 吞吐（16C64G 连续 1 小时）与 72h 内存泄漏压测需目标硬件，bench 中标记 N/A；
  - L2 抽样未接入编排器（P0 编排按 stage strategy 缩放修复轮次 3/2/1）。
