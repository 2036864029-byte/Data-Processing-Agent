---

description: "P0-B 功能模块层任务清单（B1–B9 + Plan 执行器 + 集成门禁，对应里程碑 M2 / M3）"
---

# Tasks: 插件化异构数据处理 Agent（DataAgent）— P0-B 功能模块层

**Input**: Design documents from `/specs/001-heterogeneous-data-agent/`

**Prerequisites**: `tasks.md`（P0-A 基础支撑层，A1–A9 + 集成门禁，任务 T001–T072，**先于本文件实现**）、plan.md（B1–B9 + B-PLAN 依赖列）、spec.md、research.md、data-model.md、contracts/、宪法 1.2.0

**Tests**: 宪法 G8 强制 TDD，每个模块 Phase 均含**先红后绿**测试任务；宪法六个必写测试 #2/#3/#4/#6 在 B6（repair）落地。

**Organization**: 按 plan「执行优先级与里程碑」——本文件覆盖 **P0-B 功能模块层（B1–B9）**，外加任务级执行规划 `kernel/plan.py`（B-PLAN，FR-026，依赖 B8 表达式引擎）。**任务编号承接 tasks.md 全局连续（自 T073 起）**，两份文件合为完整 P0 任务图，均在实现开始前完成。

**Scope 说明**：B1–B9 在 A1–A9 之上按依赖列顺序开发；各模块内标记 [P] 的任务可并行。P1 / P2 暂不拆解。

## Format: `[ID] [P?] [Module] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Module]**: 所属模块（B1–B9 / BPLAN），用于可追溯性；集成收尾阶段无标签
- 描述含精确文件路径

## 路径约定

- 功能域在 `src/dataagent/pipeline/` 内各占一个目录（ingest / sniff / route / profile / quality / repair / merge / output）；`platform/rules/`（RulePack）、`platform/plugins/adapters/`（引擎适配）
- `tests/`：`unit` / `integration` / `e2e` / `fault` / `bench`
- 黄金集样本（`doc/test_pdf/` 等）不入库（git 忽略，见 [[git-github-setup]] 记忆）

---

## Phase 1: B1 接入 + 文件级体检

**Goal**: 文件夹监听 / 手动上传两种接入 + 11 项文件级体检（三态，先于任何解析能力拦截）。**依赖 A1 A4。**

**Independent Test**: 放入损坏 / 加密 / 截断文件，在解析能力被调用前被拦截进带诊断的待确认队列，任务其余文件不受影响（US1 #3）；重复提交按内容哈希去重复用已有结果（US1 #2）。

### 测试（TDD：先红后绿）⚠️

- [X] T073 [B1] 体检三态测试 `tests/unit/pipeline/ingest/test_health_check.py` — 11 项体检（空 / 超大文件、魔数、完整性、交叉引用、页树、页树深度、单页尺寸、对象总数、资源嵌套深度、流压缩比、加密，FR-002）输出 `ok / repairable / rejected`；**页树深度超限必须硬拒**；可疑项降采样后继续并标记为高风险页
- [X] T074 [B1] 接入流程测试 `tests/integration/pipeline/ingest/test_ingest.py` — 文件夹监听 + 手动上传两种接入（FR-001 P0 前两种）；损坏 / 加密文件先于解析拦截进待确认队列；重复文件（同内容哈希）直接复用已有结果、不重复解析不重复计费

### 实现 B1

- [X] T075 [B1] 实现 `src/dataagent/pipeline/ingest/` 接入器 — folder watch + upload，`doc_id` 以内容哈希（SHA-256）入库，重复内容命中已有结果
- [X] T076 [B1] 实现 11 项文件级体检器 — 三态输出，**在任何解析能力被调用之前**执行，页树深度超限硬拒、可疑项降采样后继续并标记高风险页
- [X] T077 [B1] 实现待确认队列落库 — 带诊断信息（拦截原因 / 体检项证据），任务其余文件不受影响（SC-005：任何文件不得被静默丢弃）

**Checkpoint**: 体检 + 接入测试全绿 → commit + push

---

## Phase 2: B2 一级格式判定器

**Goal**: 自建一级判定器（magic / 编码 / 分隔符），按内容而非后缀输出 5 家族 + 三态，单文件 ≤50ms / ≤256KB。**依赖 A1 A2。**

**Independent Test**: CSV 改名 `.pdf` 的文件按内容判定进 CSV 链路并记录 `extension_mismatch` 事件（US1 #4）；无法分类的格式进待确认队列并告警、不得静默丢弃。

### 测试（TDD：先红后绿）⚠️

- [X] T078 [B2] 家族判定测试 `tests/unit/pipeline/sniff/test_sniffer.py` — 输出 `pdf / tabular / text / image / unknown` 五类家族；判定三态（确信 / 歧义 / 无法分类）；magic / 编码 / 分隔符各自覆盖正常与损坏样本
- [X] T079 [B2] 后缀不符与无法分类测试 — CSV 改名 `.pdf` 按内容而非后缀判定、记录 `extension_mismatch` 事件（不误判、不猜）；无法分类进待确认队列并告警
- [X] T080 [B2] 性能断言测试 — 单文件判定开销 ≤ 50ms 且最多读取前 256KB（FR-003）

### 实现 B2

- [X] T081 [B2] 实现 `src/dataagent/pipeline/sniff/sniffer.py` — 一级格式判定器（magic 探测 + 编码检测 + 分隔符统计），纯规则、不依赖任何解析插件，≤50ms / ≤256KB
- [X] T082 [B2] 实现歧义双跑决策 — 歧义时**不得直接猜**，小样本双跑比较质量与成本后再决策；无法分类归待确认队列并告警

**Checkpoint**: 判定 / 三态 / 性能断言全绿 → commit + push

---

## Phase 3: B3 两级路由 + 插件引擎接入

**Goal**: 两级路由 + `engine.pdf`（pdf-inspector，双 capability）+ `engine.csv` / `engine.text` 接入 + 批级抽取。**依赖 A2 A5 B1 B2。**

**Independent Test**: 非 PDF 格式不经 PDF 分类能力；engine.pdf 分类（不产内容）与抽取（产 artifact）经注册表分别被路由与解析消费；批量抽取默认 32 页、扫描页单独成批、批内不拆。

### 测试（TDD：先红后绿）⚠️

- [X] T083 [B3] 两级路由测试 `tests/unit/pipeline/route/test_routes.py` — 一级为边界与格式判定、**二级仅在 PDF 家族内**调用 PDF 分类做页级路由；非 PDF 不得经过 PDF 分类能力；每条路由决策带理由与决策时画像快照并落库，可事后解释（FR-004）
- [X] T084 [B3] 批级抽取测试 `tests/unit/pipeline/route/test_batching.py` — 同一文档页按批一次传入、批内不拆（默认批 32 页）、扫描页单独成批不与普通页混批、同一文档所有批落在同一处理单元（FR-006；G4 批级性能）
- [X] T085 [B3] engine.pdf 双 capability 测试 — `pdf.classify` 必须能在不产内容前提下完成判定（`markdown = None`、`cmap_gaps` 恒为空）；`pdf.text.extract` 产不可变 artifact；仅 `deterministic` 允许结果缓存（缓存键 = 内容哈希 + 参数哈希 + `id@version`，宪法原则 I）

### 实现 B3

- [X] T086 [B3] `uv add pdf-inspector` 并实现 `src/dataagent/platform/plugins/adapters/engine_pdf.py` — 双 capability 适配（分类 / 抽取），接口依据 `doc/pdf-inspector.md`；页码基准经 `contracts/pages.py` 转换（插件接口 0-indexed 与 1-indexed 混用，全工程仅一处转换点）；先确认目标平台预编译包可用（research R10）
- [X] T087 [B3] 实现 `engine_csv.py` / `engine_text.py` — manifest 契约接入（`tabular.text.extract` / `text.text.extract`）
- [X] T088 [B3] 实现 `src/dataagent/pipeline/route/routes.py` — 两级路由，每条决策带理由 + 画像快照落库
- [X] T089 [B3] 实现分批器 — 默认批 32 页、扫描页单独成批、批内不拆（文件打开次数 ≤ ⌈页数/批大小⌉ + 2，宪法性能预算）

**Checkpoint**: 路由 / 批级 / 双 capability 测试全绿 → commit + push

---

## Phase 4: B4 页面画像两段写入

**Goal**: PageProfile 两段写入（路由段 + 质量段），`route_reason` 原样保留。**依赖 A2 B3。**

**Independent Test**: 路由阶段写 `class / layout`，解析完成后写 `signals / quality`；`glyph_coverage` 仅抽取后可得（`detect_pdf` 阶段恒为空）；`route_reason` 不裁剪 / 不映射 / 不丢弃。

### 测试（TDD：先红后绿）⚠️

- [X] T090 [B4] 两段写入测试 `tests/unit/pipeline/profile/test_write.py` — 路由段（`pdf_type` / `class_confidence` / `route` / `route_reason[]` / `columns` / `has_table` / `is_complex` / `rotation`）先写；解析完成后写 `signals`（`char_density` / `glyph_coverage` / `empty_region_ratio` / `header_footer_ratio`）与 `quality`（`extract_quality` / `tier`）；`glyph_coverage` 来自 `cmap_gaps` 仅抽取后可得
- [X] T091 [B4] route_reason 原样保留测试 — 上游返回的路由原因原样落库，不裁剪 / 不映射 / 不丢弃（G2）；映射成缺陷码发生在配置表而非落库时；禁 `page_filter` 引用 `extract_quality`（因果循环）

### 实现 B4

- [X] T092 [B4] 实现 `src/dataagent/pipeline/profile/` — 两段写入器（路由段 + 质量段），`route_reason` 原样保留

**Checkpoint**: 两段写入测试全绿 → commit + push

---

## Phase 5: B5 质检（L1 全量 + L2 抽样 + 硬门控 + 性能档位）

**Goal**: L1 全量逐页确定性质检 + L2 分层抽样 + 硬门控 + 性能档位；QC 只读、结果先落库再触发下游。**依赖 A2 B4。**

**Independent Test**: 逐页校验在任何性能档位下都全量执行（禁止"跳过简单页"，G4）；`fast` 档只允许削减 L2 抽样比例与增强轮次、不得影响 L1（taskspec 校验规则 4）；QC 不重新解析、不修改产物。

### 测试（TDD：先红后绿）⚠️

- [X] T093 [B5] L1 全量硬门控测试 `tests/unit/pipeline/quality/test_l1.py` — 逐页确定性指标全量、硬门控、定位到页（FR-009）；任何性能档位下全量执行
- [X] T094 [B5] L2 分层抽样测试 — 昂贵校验分层抽样 + 低质量页全查；`profile_speed` 三档（quality / balanced / fast）只削 L2 抽样比例与增强轮次（FR-009）
- [X] T095 [B5] QC 只读测试 — 质检器不重新解析、不修改产物；质检结果**先落库再触发下游**（G5 / role-messages 三条硬性约束 #2）；缺陷必带可操作 `location`、无 location 不进修复队列只能统计

### 实现 B5

- [X] T096 [B5] 实现 `src/dataagent/pipeline/quality/l1_check.py` — 逐页确定性指标 + 硬门控（全量、定位到页）
- [X] T097 [B5] 实现 `src/dataagent/pipeline/quality/l2_sample.py` — L2 分层抽样 + 性能档位 `profile_speed`（抽样比例按档位配置）

**Checkpoint**: L1 / L2 / 只读测试全绿 → commit + push

---

## Phase 6: B6 T1 后处理 + 修复（P0 最大自建项）

**Goal**: 增强算子库（表格 cell 重建 / 页眉页脚剔除 / 跨页段重排等）+ 缺陷码映射配置 + 增强回路 + 失败页聚包 + 降级路径。**依赖 A3 B4 B5。宪法六个必写测试 #2/#3/#4/#6 全部在此落地。**

**Independent Test**: 六个必写测试 #2–#4、#6 全绿；同一缺陷码同页只修一次、无进展立即停机、不无限重试；采纳候选必须带来可量化指标提升（≥5%）且不修改原产物。

### 测试（TDD：先红后绿）⚠️

- [X] T098 [B6] 必写测试 #2 未知原因透传 — 映射表外 `reason` → `plugin_reason_unknown`，`raw_reason` **原样保留**，不得丢弃、不得凭猜测归入邻近码（G2）
- [X] T099 [B6] 必写测试 #3 算子不可变且幂等 — 原产物不变、两次调用候选一致（算子返回新对象，禁修改原产物）
- [X] T100 [B6] 必写测试 #4 无进展停机 — `OperatorResult.changed == false` 立即放弃、不重试第二次、不得重试同参数（G5）
- [X] T101 [B6] 必写测试 #6 增强回路收敛 — 同码唯一、三层预算（成本 / 次数 / 耗时）耗尽后升级、无无限循环
- [X] T102 [B6] 增强器降级测试 — 外部增强器不可用时按配置降级（**默认仅标记 mark_only**），报告显式声明所用降级路径（FR-013）；`fallback.enhancer` 必须是配置项不得写死（taskspec 校验规则 5）
- [X] T103 [B6] 失败页聚包测试 — 散落失败页聚合成包（50–100 页一包）送外部增强器，只抽失败页不重跑整个文件；页码映射落盘并参与幂等（FR-012）

### 实现 B6

- [X] T104 [B6] 实现 reason→defect_code 配置映射表 — 可配、按插件版本分组；未知 reason 一律落 `plugin_reason_unknown`；`defect_code` 不反推 `reason`（单向）；映射只做一次（defect-codes.md 两张映射表）；`plugin_reason_unknown` 计数进运营指标、单次任务占比 > 5% 触发告警（接入 A9 T067 告警，US4 #3）
- [X] T105 [B6] 实现 T1 后处理算子库 `src/dataagent/pipeline/repair/operators/` — `rebuild_table_cells` / `strip_header_footer` / `merge_cross_page_table` / `reorder_blocks` / `resegment_columns` / `rotate_page` / `deskew` / `extract_region` / `ocr_page`（默认算子与档位逐字引用 defect-codes.md）；算子幂等、不改原产物、**禁止 IO**（G9 grep 门禁）
- [X] T106 [B6] 实现增强回路 — 同码唯一、三层停机（`changed == false` 停机）、预算耗尽升级；采纳候选必须带来可量化指标提升（≥5%）
- [X] T107 [B6] 实现失败页聚包 + 降级路径 — 50–100 页聚包送增强器 + 页码映射落盘；降级框架（`mark_only` 默认 / `local_ocr` / `fail_doc`）；`local_ocr` 增强器接入属 P1，P0 只做降级框架；**复杂页收口（抽子集 → 聚包 → 页码回填）归 B6**（data-model PageGap 进入收口流程）

**Checkpoint**: 四个必写测试 + 降级 / 聚包测试全绿 → commit + push

---

## Phase 7: B7 合并 + 分级交付 + 报告 + 输出

**Goal**: 页级合并（页码重排 / 跨页表 / 标题层级 / 文档级校验）+ 分级交付（三档）+ 分层报告 + 输出格式。**依赖 A4 B5 B6。**

**Independent Test**: 缺口页占比 0% / 3% / 8% → 交付等级 `complete` / `partial_with_gaps` / `needs_review`，`needs_review` 强制进入人工队列；缺口页保留占位、不导致页码错乱；总报告为小型报告（核心数字一页）、明细仅含缺口文件。

### 测试（TDD：先红后绿）⚠️

- [X] T108 [B7] 合并测试 `tests/unit/pipeline/merge/test_merger.py` — 按页码重排、跨页表合并依据相邻页表头列名相似度与列一致性、标题层级重建、文档级校验（FR-017）；缺口页保留占位不导致页码错乱（US2 #1）
- [X] T109 [B7] 分级交付测试 `tests/unit/pipeline/output/test_delivery.py` — 缺口页占比约 0% / 3% / 8% → `complete` / `partial_with_gaps` / `needs_review`；`needs_review` 强制进入人工队列；缺口标记内嵌在产物中、不得跳过（FR-018）；**L4 人工例外经 needs_review 强制进人工队列**
- [X] T110 [B7] 分层报告测试 `tests/unit/pipeline/output/test_reports.py` — 总览一页 + 按原因聚合 ≤ 10 行 + 文件清单 + 按需明细；**默认只输出"有缺口的文件"明细，不预生成巨型页级明细**（FR-019）

### 实现 B7

- [X] T111 [B7] 实现 `src/dataagent/pipeline/merge/merger.py` — 页码重排 + 跨页表合并（列名相似度与列一致性）+ 标题层级重建 + 文档级校验（文档级校验调用 B8 规则引擎 `checks[]`，业务勾稽，SC-013）
- [X] T112 [B7] 实现 `src/dataagent/pipeline/output/delivery.py` — 分级交付三档 + 缺口占位内嵌产物
- [X] T113 [B7] 实现 `src/dataagent/pipeline/output/reports.py` — 分层报告（总览 / 聚合 / 清单 / 按需明细）
- [X] T114 [B7] 实现输出格式 — JSON / CSV / Markdown / Excel（openpyxl，`uv add openpyxl`）+ 数据库写入（经 `platform/store/sql/`），全部为 P0 交付格式（FR-018）

**Checkpoint**: 合并 / 分级 / 报告测试全绿 → commit + push

---

## Phase 8: B8 RulePack + 受限表达式引擎

**Goal**: 声明式规则包（纯数据、可版本化、可回滚、dry-run）+ json-logic 受限表达式引擎 + 字段提取声明式层（P0）。**依赖 A2 B3。**

**Independent Test**: 新增规则 ≤ 5 分钟生效、无需重启；冲突规则发布被阻断；回滚立即生效且可审计；`scope.page_filter` / `skip_pages` 与 RulePack 用**同一套**受限表达式引擎、白名单仅限统计指标与 `class / layout / signals`、禁引用 `extract_quality`。

### 测试（TDD：先红后绿）⚠️

- [X] T115 [B8] 规则包测试 `tests/unit/platform/rules/test_rulepack.py` — YAML 纯数据、JSON Schema 校验、冲突发布阻断、回滚、dry-run（FR-014）；每条规则可追溯原始需求文本（FR-016 编译链路为 P1，P0 只做规则包本身）
- [X] T116 [B8] 受限表达式引擎测试 — 白名单字段 / 白名单函数、沙箱求值**无代码执行**；`page_filter` / `skip_pages` 与 RulePack 同一套引擎；**禁 `page_filter` 引用 `extract_quality`**（因果循环，taskspec 校验规则 3）

### 实现 B8

- [X] T117 [B8] 实现 `src/dataagent/platform/rules/rulepack.py` — 规则包校验 / 版本化 / 回滚 / dry-run
- [X] T118 [B8] 实现 `src/dataagent/platform/rules/expr.py` — json-logic 受限表达式引擎（沙箱求值、白名单字段 / 函数）
- [X] T119 [B8] 实现 `src/dataagent/platform/rules/extract.py` — 字段提取三层之声明式锚点层（P0 只做声明式层；模板按坐标 / 模型兜底为后续）

**Checkpoint**: 规则包 / 表达式测试全绿 → commit + push

---

## Phase 9: B-PLAN 任务级执行规划（kernel/plan.py，FR-026）

**Goal**: Plan 解析 / 冻结 / 分支求值；3 种 stage kind（processing / decision / replan）、禁自由图结构；同一 `plan_version` 重放逐字一致。**依赖 A2 A8 B8（分支表达式）。**

**Independent Test**: 分支表达式引用未知 / 不存在统计指标在生成期即拒绝；`plan` 一经确认即冻结、执行与重放逐字一致（SC-014）；重规划产生新 `plan_version`、计入 `max_replans`（默认 3）。

### 测试（TDD：先红后绿）⚠️

- [ ] T120 [BPLAN] Plan 解析 / 冻结 / 分支求值测试 `tests/unit/kernel/test_plan.py` — 3 种 stage kind 白名单（`processing` / `decision` / `replan`）、禁止自由图结构（PRD 4.1.9）；分支表达式用受限表达式引擎、白名单仅限统计指标、引用未知指标生成期拒绝（taskspec 校验规则 8）；`guards` 三项硬上限：`max_model_calls`（默认 8）/ `max_replans`（默认 3）/ `max_wall_time_s`（默认 3600），超出即停止交付带缺口结果（校验规则 9）；冻结后不可改；同一 `plan_version` 重放逐字一致

### 实现 B-PLAN

- [ ] T121 [BPLAN] 实现 `src/dataagent/kernel/plan.py` — Plan 解析 / 冻结 / 分支求值；分支条件复用 `platform/rules/expr.py` 受限表达式；重规划产生新 `plan_version` 可 diff

**Checkpoint**: Plan 冻结 / 重放测试全绿 → commit + push

---

## Phase 10: B9 黄金集 + 标定 + 评测

**Goal**: 200 页黄金集 + `extract_quality` 标定 + 评测门禁（STP ≥ 80%、误放率 ≤ 2%、QC 自身达标）。**依赖 B5。**

**Independent Test**: 200 页黄金集（纯文本 / 单表 / 跨页表 / 多栏 / 扫描 / 编码损坏**各 ≥ 20 页**）；`fidelity` 与人工分 Spearman > 0.8；`extract_quality` 与人工"需介入"一致率 ≥ 0.85；QC precision ≥ 0.95、recall ≥ 0.90（SC-012）。

### 测试（TDD：先红后绿）⚠️

- [ ] T122 [B9] 黄金集建设 — 200 页黄金集（六类各 ≥ 20 页）：本地样本（`doc/test_pdf/`，**不入库**）+ 构造样本（表格跨页 / 页眉页脚 / 分栏错序，research R6）；人工四维打分；样本与标注保持本地私有
- [ ] T123 [B9] `extract_quality` 标定脚本 `tests/eval/` — 权重拟合 + 阈值工作点（R2）；`fidelity` 与人工分 Spearman > 0.8、一致率 ≥ 0.85
- [ ] T124 [B9] 评测脚本 + 指标门禁 — STP ≥ 80%（SC-003）、误放率 ≤ 2%、QC precision ≥ 0.95 / recall ≥ 0.90（SC-012）、文档级勾稽通过率 ≥ 99%（SC-013）；**文本还原 CER ≤ 2%（简单页）/ ≤ 5%（中等页）、表格结构 TEDS ≥ 0.80（原生）/ ≥ 0.85（含后处理）、标题层级准确率 ≥ 0.78、关键字段 exact match ≥ 98%（SC-006）**；`fast` 档 STP 报告必须标注"不可用于验收"（宪法复现性承诺）

**Checkpoint**: 标定 / 评测门禁达标 → commit + push

---

## Phase 11: P0-B 集成门禁与收尾

**Purpose**: 证明 P0 功能链路端到端成立（quickstart S1–S4 + S6），全部必写测试与工程门禁落地，P0 全量交付。

**Independent Test**: quickstart S1–S4 + S6 全部跑通；六个必写测试全绿；`dataagent bench` 性能门禁达标（单页 p50/p95、内存峰值 ≤ 50 MB、OOM 降批）。

- [ ] T125 跑 P0 端到端验证（quickstart S1–S4 + S6）：100 份混合样本任务 → 报告核对（无静默丢弃、重复去重、损坏拦截、后缀不符按内容判定、kill -9 续跑）；复杂页标记 + 分级交付 + 诊断卡；规则包生效 / 回滚；报告分层 + 决策链还原；NL→Plan 探路 → `decision` 分支 → 批量（FR-026）；**断网验证覆盖 mark_only 降级路径（本地 OCR 属 P1，SC-008 全文满足随含 OCR 增强器接入后验证）**
- [ ] T126 六个必写测试全量盘点 — #1 页码基准（A3）、#5 主键内容哈希 / 幂等键（A2/A4）、#2 未知原因透传、#3 算子幂等、#4 无进展停机、#6 回路收敛（B6）**六条全部落地并通过**（quickstart「回归验证」表）
- [ ] T127 性能与稳定性门禁 `dataagent bench` — 单页 p50 ≤ 120ms / p95 ≤ 250ms（端到端）；单进程峰值内存 ≤ 50 MB（批 32 页）；批大小 / worker 数按公式计算、禁写死 `cpu_count()`；文件打开次数 ≤ ⌈页数/批大小⌉ + 2；OOM 自动降批 32→16→8；强杀可续跑（宪法性能预算 + SC-011）；**吞吐 ≥ 30 万页/日/节点（16C64G，连续 1 小时压测取稳定值，SC-001）；72 小时压测无内存泄漏（内存增长 < 5%，SC-013）**
- [ ] T128 [P] 文档收尾 — `docs/CHANGELOG.md` 补录三类可复现性变更（插件版本 pin / 阈值 / 缺陷码增减）、ADR 记录 P0-B 关键实现决策、README 更新（完整快速开始）
- [ ] T129 全量回归 + 交付 — CI 四件套 + import-linter + 全部 grep 门禁一次跑绿；最终 commit + push；P0 全量交付对照验收锚点（plan 7.1 #9–#14 / #17 / #22–#28 / #29 + 7.2 全部）

**Checkpoint**: P0 端到端全绿 → **P0 交付完成**（P1 增强层里程碑 M4 起另行规划）

---

## Phase 12: P0 修订 — 隐藏文本层复用与 PDF 适配器质量增强

**Goal**: 修正 PDF 适配器的三个实测问题：(1) 扫描件带隐藏 OCR 文本层时被误判 OCR，应先探测复用；(2) 抽取阶段才能拿到 layout（`detect_pdf` 不填表格/分栏/编码），需在画像质量段回填；(3) `extract_pages_markdown` 遇任一 `U+FFFD` 清空整页、标题层级弱于文档级 `process_pdf`，需按需标题标定与文本恢复。**不改变五层架构、状态机、调度器、存储抽象与插件契约。**

**Independent Test**: 574 页扫描书（含隐藏文本层）可用页不再送 OCR 且路由为 native；纯扫描页仍走 OCR；恢复页在画像/档位/报告中可追溯；正常招标文档不触发第二遍解析；全量回归与六项门禁保持全绿。

### 测试（TDD：先红后绿）⚠️

- [ ] T130 [R1] 文本层探测与质量门测试 `tests/unit/platform/plugins/adapters/test_text_layer.py` — 三类样本：隐藏 OCR 文本层（本语料 574 页书）、纯扫描无文本层、正常文本 PDF；质量门（字符数 ≥ 50、`U+FFFD` 占比 ≤ 1%、可打印率 ≥ 95%）；逐页判定与文档级汇总；不可用时明确返回 absent
- [ ] T131 [R1] engine.pdf 恢复测试 `tests/unit/platform/plugins/adapters/test_engine_pdf_recovery.py` — `invisible_text_layer` 页探测成功后产出文本 artifact、`warnings` 标记 `text_layer_recovered`、不再标记需 OCR；探测失败仍保留 `needs_ocr`
- [ ] T132 [R2] 画像/路由/档位接入测试 — 抽取后 layout 回填（表格/分栏/旋转/复杂度）；文本层恢复页 route=native 且理由含 `text_layer_recovered`；`RuleTierPolicy` 不再对恢复页强制 T3；L1 输入来自恢复文本
- [ ] T133 [R3] `process_pdf` 标题标定测试 — 仅在乱码页/标题异常时触发；标题行按规范化文本 + 页顺序回填；`process_pdf` 1-indexed 与 extract 0-indexed 语义隔离；标定失败标记 `heading_calibration_failed` 且不阻塞
- [ ] T134 [R4] 黄金集口径更新测试 — manifest 增加 `text_layer` / `text_chars` / `bad_ratio`，扫描类拆分 `invisible_text_layer` 与 `pure_scan`；恢复率与恢复页 CER 进入自动评测

### 实现

- [ ] T135 [R1] `uv add pypdfium2`（runtime）并实现 `src/dataagent/platform/plugins/adapters/text_layer.py` — PDFium 逐页读取隐藏文本层 + 质量门 + provenance
- [ ] T136 [R1] 接入 `engine_pdf.py` — `invisible_text_layer` 页恢复文本并写入 `PageArtifact.warnings` / `text_layer`；纯扫描页不恢复
- [ ] T137 [R2] 实现画像质量段 layout 回填 + 路由 override + 档位例外 + L1 输入 — `pipeline/profile/writer.py`、`pipeline/route/routes.py`、`kernel/policies/rule_tier.py`、`pipeline/quality/l1_check.py`
- [ ] T138 [R3] 实现 `src/dataagent/platform/plugins/adapters/heading_calibration.py` — 按需 `process_pdf` 标题标定 + 失败兜底；接入 `engine_pdf.py`
- [ ] T139 [R4] 更新黄金集/查看器/评测与文档 — `tests/eval/*`、`docs/ADR/0005-hidden-text-layer.md`、`docs/CHANGELOG.md`、README；全量回归（CI 四件套 + import-linter + grep 门禁）并 commit + push

**Checkpoint**: R1–R4 全部验收 → P0 修订完成 → 回到 Phase 11 集成门禁

---

## Dependencies & Execution Order

### Phase Dependencies

- **B1（Phase 1）**: 依赖 A1 A4
- **B2（Phase 2）**: 依赖 A1 A2
- **B3（Phase 3）**: 依赖 A2 A5 B1 B2
- **B4（Phase 4）**: 依赖 A2 B3
- **B5（Phase 5）**: 依赖 A2 B4
- **B6（Phase 6）**: 依赖 A3 B4 B5
- **B7（Phase 7）**: 依赖 A4 B5 B6
- **B8（Phase 8）**: 依赖 A2 B3
- **B-PLAN（Phase 9）**: 依赖 A2 A8 B8（分支表达式引擎）
- **B9（Phase 10）**: 依赖 B5
- **Phase 11（集成门禁）**: 依赖全部 B 模块完成
- **Phase 12（P0 修订）**: R1 依赖 B3 A5；R2 依赖 R1 B4 B5 A8；R3 依赖 R1；R4 依赖 R1–R3；完成后回到 Phase 11 联测

### 并行机会

- B1 与 B2 无相互依赖，可并行；B8 依赖 A2 + B3，可与 B4/B5 并行推进
- B6 是 P0 最大自建项（算子库 + 回路），建议单独占满一个开发时段
- B9 黄金集建设（T122）可与 B6/B7 并行开始（标注不依赖质检实现完成后的真实数据，但标注数据质量依赖 B5）
- 集成门禁前各模块独立跑绿，统一在 Phase 11 联测

### 每个 Phase 内

- 测试（TDD）MUST 先写、先 FAIL 再实现
- pipeline（处理阶段）→ platform（规则 / 适配）→ kernel（内核规划）→ 集成
- 一个 Phase 完成且独立验收通过，再进入下一个

## Parallel Example: B3 路由 + 引擎接入

```bash
# B3 内三个实现可并行（不同文件、无相互依赖）：
Task: "实现 src/dataagent/platform/plugins/adapters/engine_pdf.py"
Task: "实现 src/dataagent/platform/plugins/adapters/engine_csv.py"
Task: "实现 src/dataagent/pipeline/route/routes.py"
```

## Implementation Strategy

### 增量交付（模块一个接一个，P0-A 之后）

1. 前提：`tasks.md`（P0-A）全部完成 + 集成门禁通过
2. B1 → B2（可并行）→ B3 → B4 → B5 → B6 → B7；B8 与 B4/B5 并行；B9 与 B6/B7 并行
3. B-PLAN（kernel/plan.py）在 B8 之后
4. **STOP and VALIDATE**: 跑 Phase 11 P0 端到端 → 通过即 P0 交付完成
5. P1 增强层（M4 起）另行规划，不在本文件

### 并行团队策略（多人时）

1. A 层已就绪后：开发者 A 走 B1→B3→B4→B5 主线；开发者 B 并行 B2 与 B8
2. B6（算子库）独立占一个开发者；B7 在 B5/B6 之后
3. B9 标定脚本可与主线并行开发，等 B5 真实输出后灌入标定

---

## Notes

- [P] 任务 = 不同文件、无依赖
- [B1]–[B9] / [BPLAN] 标签将任务映射到对应模块（集成收尾阶段无标签）；[R1]–[R4] 为 Phase 12 P0 修订阶段标签
- 每个模块独立可测：完成即跑该模块 Independent Test
- **TDD 铁律**：测试先写、先红后绿，不得先实现后补测试（宪法 G8 NON-NEGOTIABLE）
- **每模块即推送**：每个 Phase 完成后 commit + push（依赖全局代理 `127.0.0.1:7890`）
- 本文件与 `tasks.md`（P0-A）合为**完整 P0 任务图**，均在实现开始前完成；任务编号全局连续（T001–T129）
- 六个必写测试映射：#1 A3 / #5 A2·A4 / #2·#3·#4·#6 B6，见 `tasks.md` T069 盘点与 Phase 11 T126
- P1 / P2（M4 起）暂不拆解，另行规划
