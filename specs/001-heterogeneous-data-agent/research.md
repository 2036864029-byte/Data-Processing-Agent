# Research & Technical Decisions: 插件化异构数据处理 Agent（DataAgent）

**Created**: 2026-10-30 | **Spec**: specs/001-heterogeneous-data-agent/spec.md | **Plan**: plan.md

## 说明

PRD V2.3 与宪法已锁定绝大多数技术方向，本文件不重复论证已锁定的选型，只记录：(1) 实现期需要进一步研究 / 实测的未知项；(2) 每个关键决策的 Decision / Rationale / Alternatives。宪法是最高约束，本文件结论不得与宪法冲突。

## 已锁定选型（记录，非新决策）

| 项 | 选型 | 锁定来源 |
| -- | ---- | ---- |
| 语言 / 包管理 | Python 3.12；uv 唯一 | 宪法「技术栈 / 包管理器约束（强制）」 |
| PDF 引擎 | pdf-inspector（`engine.pdf` 插件，双 capability） | 宪法 + PRD 附录 C |
| 状态与审计 | PostgreSQL（标准 SQL 子集） | 宪法 + PRD 4.8.7 |
| 队列 | Redis Streams（最小语义：至少一次 + 手动确认 + 死信） | 宪法 + PRD 4.8.7 |
| 对象存储 | MinIO + 本地盘双实现 | 宪法 + PRD 4.8.7 |
| 配置 | pydantic-settings（单一 schema） | 宪法 |
| 受限表达式 | json-logic（RulePack / `page_filter`，沙箱求值） | 宪法 |
| 迁移 | Alembic（所有 DDL 走迁移） | 宪法 |
| 测试 | pytest + hypothesis + 自建 eval | 宪法 |
| 并发 | 进程池（CPU 密集路径禁线程池 / 事件循环） | 宪法 + PRD 4.3.8 |
| 批量 | 批级并行，默认 32 页，文件内按批串行 | PRD 4.3.4 / 4.3.8 |

## 关键决策

### D1. `engine.pdf` 适配器与页码基准统一

- **Decision**: 在 `platform/plugins/adapters/pdf.py` 封装 pdf-inspector 为 `engine.pdf`；**页码字段级转换**（0-indexed ↔ 1-indexed）只发生在 `contracts/pages.py` 一处，系统内部一律 1-indexed。
- **Rationale**: pdf-inspector 不同接口、甚至同一返回对象内部页码基准不一致（`extract_pages_markdown` 的 `pages[].page` 是 0-indexed，而同对象里的 `pages_with_tables` 是 1-indexed）；按字段级而非接口级转换是唯一正确做法（PRD 4.3.3 / D.6）。批量抽取必须一次传多页（`pages=[...]`），逐页调用会放大文件级固定开销。
- **Alternatives considered**: 直接调用 pdf-inspector 原始接口（页码错乱风险）、接口级统一转换（仍会差一页）→ 均拒绝。
- **验证锚点**：断言式单元测试 `extract_pages_markdown(path, pages=[k])` 后 `pages[0].page == k` 且 `k+1 in pages_with_tables`（宪法六个必写测试 #1）。

### D2. `ocr_reason` 枚举表（开放研究项，实现期必做）

- **Decision**: 作为实现期第一个研究任务：用 50–100 份真实样本（覆盖纯文字 / 扫描 / 双栏 / 表格 / 混合 / 损坏编码）全量跑 `detect_pdf` + `extract_pages_markdown`，收集两侧全部 `ocr_reason` / `reasons[]` 字符串，去重成表并标注语义与对应缺陷码；作为配置文件初始内容、按插件版本分组；未知项透传 `plugin_reason_unknown`。
- **Rationale**: PRD D.6 明确「枚举表来自样本实测，不来自文档」——该字段文档只声明为 machine-readable，未给枚举；跳过此步则映射表只能靠猜，猜错的表现（缺陷码归错、算子用错、报告写错）都不报错。
- **Alternatives considered**: 手写猜测映射表 → 拒绝（插件升级后静默失效）。
- **依赖语料**: 本地 `doc/test_pdf/`（62MB 样本，不入库）可作为样本来源。

### D3. 黄金集与标定

- **Decision**: 建设 200 页黄金集（纯文本 / 单表 / 跨页表 / 多栏 / 扫描 / 编码损坏各 ≥ 20 页），人工按四维（文本 / 结构 / 语义 / 版式）0–3 分打分；拟合 `extract_quality` 权重与 `fidelity` 权重；扫阈值画 STP-误放率曲线选工作点（STP ≥ 80%、误放 ≤ 2%）。
- **Rationale**: PRD 4.4.7 标定流程是"质量可证明"（P5）的唯一实现路径；`fidelity` 与人工总分 Spearman > 0.8、`extract_quality` 与人工"需介入"一致率 ≥ 0.85 是验收项 7.2 #1–#2。
- **Alternatives considered**: 凭经验设阈值 → 拒绝（上线即可能不达标，PRD V2.1 教训）。

### D4. 插件注册表形态（热 / 冷路径）

- **Decision**: P0 做**元数据注册表**（能力索引 / 启停 / 版本锁定 / 依赖与兼容性校验 / 多实现显式优先级），**不做独立网关**。热路径插件（`engine.pdf`、T1 算子）同进程直接调用；冷路径插件（OCR / 外部增强器 / connector / exporter / extractor）子进程沙箱。
- **Rationale**: PRD 5.5 成本表——网关每页多 1–3ms（共享内存）或 15–25ms（管道）序列化开销，直接抵消 10–25ms/页的成本优势；"企业级"的定义是可审计 / 可回滚 / 可灰度 / 故障可隔离，靠注册表 + span + 版本锁定实现，不需要网关。
- **Alternatives considered**: 独立网关（P0）→ 拒绝（成本与复杂度不成比例）；把注册表做进路由模块 → 拒绝（职责分离）。

### D5. 并发与 worker 模型

- **Decision**: worker 进程数 = `min(核数 × 1.25, 可用内存 / 单进程上限, 32)`；批大小默认 32（小规格降档，最低 10）；文件之间并行、文件内按批串行；同文档所有批落同一节点（`doc_affinity_key`）；OCR 页单独成批。
- **Rationale**: PRD 4.3.8——吞吐来自进程数而非事件循环（1 核 ≈ 66 页/秒，16 核 ≈ 1000 页/秒）；Rust 扩展崩溃会杀整个进程，必须进程池隔离（宪法 VI）。
- **Alternatives considered**: 页级 fan-out、线程池、异步改造 → 全部拒绝（PRD 明文）。

### D6. 存储抽象边界

- **Decision**: 对象存储做抽象（`put/get/exists/delete/list`，本地盘 + S3 两实现）；队列只依赖最小语义（至少一次 + 手动确认 + 死信），禁延迟消息与优先级队列（应用层用 `visible_at` 实现）；关系库不做运行时切换，从第一天遵守标准 SQL 子集；页产物与文档主键 = SHA-256 内容哈希。
- **Rationale**: PRD 4.8.7 三档可替换性不同；内容寻址 key 换存储底座不变，决定三年后能否平滑换底座。
- **Alternatives considered**: 全量抽象（含关系库）→ 拒绝（P0 无第二个真实实现，抽象大概率是错的）；自增 ID → 拒绝（换底座时映射错乱）。

### D7. 续跑与状态

- **Decision**: 落盘分层（PG 状态 / 对象存储产物 / 本地临时盘 / Redis 队列心跳）；批级事务边界（产物 + 状态都成功才完成）；先写内容再写索引；恢复从 `resume_point` 后的批继续、已完成批 0 重做；心跳按"进展"而非"耗时"判定（连续 60s 无心跳才判死亡）。
- **Rationale**: PRD 4.8.5 核心承诺「worker 随时可被强杀、换一个接着做、结果不变」；按批续跑（200 页文件最多损失一批）优于按文件续跑（重做 200 页）。
- **Alternatives considered**: 进程内状态（asyncio.Task / 线程字典 / 内存 future）→ 宪法明文禁止作为唯一状态源。

### D8. 性能档位与 L1 不可削减

- **Decision**: `profile_speed = quality | balanced | fast`；唯一合法削减顺序：L2 抽样比例 → 增强轮次 → 增强档位；**L1 在任何档位下全量**。
- **Rationale**: PRD 4.4.12——L1 决定"哪几页有问题"，不全量则后续修复 / 标记 / 报告全部失去依据；`fast` 档 STP 报告必须标注"不可用于验收"。
- **Alternatives considered**: 用 fast 档跳过简单页 → 拒绝（跳过的是漏检率高发区，PRD 4.4.11）。

### D9. 规则引擎

- **Decision**: json-logic 受限表达式（白名单字段 / 白名单函数，沙箱求值，无代码执行）；RulePack 与 `scope.page_filter` 共用同一引擎；禁 `page_filter` 引用 `extract_quality`（因果循环）。
- **Rationale**: PRD 4.6.2 / 4.1.4 规则 3；宪法「受限表达式」。
- **Alternatives considered**: 开放任意代码 → 拒绝（用户侧只能写数据不能写代码，PRD 4.6.4）。

### D10. 为什么不用图编排框架（LangGraph / pydantic_graph）

- **Decision**: 不引入图编排框架；Plan 的 3 种 stage kind（processing / decision / replan）由 `kernel/plan.py` 自实现，禁止自由图结构。
- **Rationale**: 框架的 durable execution 与既有续跑设计（`resume_point` + 批级事务 + outbox，FR-020 / SC-010）是**两套状态源，只能二选一**；选框架等于推翻续跑承诺。自实现 3 种 stage kind 可保证"同一 `plan_version` 重放逐字一致"。
- **Alternatives considered**: LangGraph、pydantic_graph → 拒绝（状态源冲突 + 可复现性失控风险）。

### D11. Pydantic AI 引入边界（任务级模型调用）

- **Decision**: **仅** `kernel/policies/model_*.py`（3 个任务级模型实现）内部使用；不用于编排 / 状态 / 续跑；锁精确版本进 `uv.lock`；CI 门禁 `grep -rn "pydantic_ai" src/ | grep -v "kernel/policies/model_"` 非空即失败。
- **Rationale**: 提供结构化输出校验、类型安全、失败重试、调用计量；框架 API 变更风险通过「锁版本 + 仅 3 文件引用 + 失败可回退裸 HTTP（Protocol 不变）」控制（规划 R4）。
- **Alternatives considered**: 裸 HTTP 调用（保留为回退路径）；LangChain（宪法禁止引入）。

### D12. 扫描件隐藏文本层的探测与复用（P0 修订）

- **Decision**: 扫描件先探测是否存在隐藏 OCR 文本层：用 PDFium（`pypdfium2`）逐页读取文本，按质量门（字符数 ≥ 50、`U+FFFD` 占比 ≤ 1%、可打印率 ≥ 95%）判定；达标则复用文本层（`text_layer=invisible_recovered`、`route=native`、产物 `warnings` 标记），不送 OCR；不达标才置 `needs_ocr` 并进入 T3/聚包。
- **Rationale**: 实测本语料 574 页扫描书在 pdf-inspector 中只返回图片占位（`[Image: Im0]`），`extract_pages_markdown` / `process_pdf` 均为空、原因 `invisible_text_layer`；但 PDFium 可读出每页 122–1871 字（全书 42 个 `U+FFFD`），WPS 能复制也证明隐藏层存在。直接送 OCR 是 574 页 × 100–300ms 的纯浪费，且违背“先复用已有信息”的降级顺序。
- **Alternatives considered**: 直接全量 OCR → 拒绝（成本高、隐藏层已可用）；只用 pdf-inspector → 拒绝（它忽略不可见文本，拿不到）；`pypdf` 纯 Python 解析 → 暂不采用（CJK ToUnicode 支持与性能未验证）。PDFium 与 pdf-inspector 同源，行为可解释。
- **Consequence**: 新增运行时依赖 `pypdfium2`；隐藏层通常只有原始行、无标题层级，需 T1 编号规则重建；质量门阈值需黄金集标定（R7）。

### D13. pdf-inspector API 语义差异与按需标题标定（P0 修订）

- **Decision**: 逐页产物继续用 `extract_pages_markdown`（唯一提供页边界）；仅在“乱码页多 / 标题数异常”时追加一次整本 `process_pdf`，用于文档级标题回填与被抑制页文本恢复；失败标记 `heading_calibration_failed`，不阻塞交付。禁止逐页调用 `process_pdf`。
- **Rationale**: 实测同一 774 页书：`process_pdf(整本)` 识别 199 个标题，`extract_pages_markdown(整本)` 仅 105 个；32 页批上 process 9/3/10/5 vs extract 7/3/5/4，说明 `process_pdf` 使用文档级样式统计。`process_pdf` 的 `pages` 为 **1-indexed**，多页返回单一拼接 Markdown、**无页分隔**；逐页调用会退化为 extract 层级且放大文件打开次数（PRD 批级预算）。实测耗时（774 页）：extract 整本 3.39s、process 整本 3.39s、text items 2.75s，按需双遍仍远低于端到端 150ms/页。
- **Alternatives considered**: 逐页 `process_pdf` → 拒绝（标题收益消失、性能差）；`process_pdf` 作为唯一产物来源 → 拒绝（无页边界、全本常驻内存）；接受 extract 的 105 个标题 → 拒绝（层级质量不足）。
- **Consequence**: 需要“标题行 → 页”的规范化文本匹配（重复标题/跨页标题是风险，进入 R8）；只有病态文档付第二遍成本。

## 开放研究项（进入实现期）

| # | 研究项 | 目标产出 | 依据 |
| -- | ------ | -------- | ---- |
| R1 | `ocr_reason` 枚举采集（约半天） | `reason → defect_code` 配置初始表（按插件版本分组） | PRD D.6 |
| R2 | 黄金集建设与人工标注（需人工参与） | 200 页四维打分 + 权重拟合 + 阈值工作点 | PRD 4.4.7 / 7.2 |
| R3 | pdf-inspector 目标平台性能实测 | 分类 / 抽取 p50-p95 基线；批大小与内存验证 | PRD 4.3.3 / 6.1 / 宪法「性能与稳定性预算」 |
| R4 | 外部二进制可用性验证（R10） | PDFium / ONNX Runtime / OCR 模型在 Linux（含潜在 ARM）的预编译包与许可证清单 | PRD R10 / 5.4 |
| R5 | 一级判定阈值标定（真实语料） | 家族阈值表初值 + `extension_mismatch` 观测口径 | PRD 4.2.5 |
| R6 | 复杂页高发缺陷语料构造 | 表格跨页 / 页眉页脚 / 分栏错序的构造样本（供 T1 与故障注入用） | PRD 4.4.11 / 6.3 |
| R7 | 隐藏文本层质量阈值标定 | 字符数 / `U+FFFD` / 可打印率阈值与人工“可用”判定的一致率 | PRD 4.3.3 / 4.5.11 |
| R8 | 标题→页匹配准确率 | 重复标题 / 跨页标题下的匹配错误率；`process_pdf` 触发阈值 | PRD 4.7.1 / 本次实测 |

## 结论

- 所有 NEEDS CLARIFICATION 已消除：技术选型与约束由宪法 + PRD 锁定，无未决的架构分歧。
- R1–R6 为**实现期研究任务**，不阻塞 Phase 1 设计（data-model / contracts / quickstart 已按上述决策产出）。
