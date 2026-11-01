# Data Model: 插件化异构数据处理 Agent（DataAgent）

**Created**: 2026-10-30 | **Spec**: specs/001-heterogeneous-data-agent/spec.md | **Plan**: plan.md

## 主键铁律（NON-NEGOTIABLE）

> **文档与页产物的主键必须是内容哈希（SHA-256），不得使用自增 ID。**

内容寻址的 key 在更换存储后端时不变、搬数据安全；用自增 ID 再映射到对象 key，换底座时整个映射会错乱（PRD 4.8.7 / 5.2）。禁止自增序列做业务主键。

## 实体

### Task（任务）

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `task_id` | uuidv7 | 任务标识 |
| `spec` | TaskSpec | 任务规格（见 contracts/taskspec.md） |
| `state` | 枚举 | 状态机状态（见下） |
| `budget` | 结构 | 成本 / 次数 / 耗时三层预算 |
| `resume_point` | 批引用 | **最后完成的批**，恢复时从其后继续（PRD 4.8.5） |

**规则**：TaskSpec 必须过 JSON Schema 校验；生成后先预览（文件列表 / 前 20 行）、人工确认后才执行（L1 及以上）。

### Document（文档）

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `doc_id` | **sha256** | 内容哈希主键 |
| `family` | 枚举 | 一级判定结果：`pdf` / `tabular` / `text` / `image` / `unknown` |
| `page_count` | int | 页数 |
| `profile` | PageProfile 引用 | 文档级画像聚合 |
| `health` | 枚举 | 文件体检三态：`ok` / `repairable` / `rejected` |

**规则**：`family` 与 `health` 由一次扫描同时产出（PRD 4.2.5 规则 3）；重复文件（同内容哈希）直接复用已有结果。

### PageProfile（页面画像）

分两次写入（PRD 4.3.5）：

| 分组 | 字段 | 写入时机 |
| ---- | ---- | ---- |
| `class` | `pdf_type`、`class_confidence`（文档级）、`route`（native/ocr/vlm）、`route_reason[]`（机器可读，**原样保留**） | 路由阶段（`detect_pdf` 后） |
| `text_layer` | `present` / `invisible_recovered` / `absent`；附 `text_chars`、`bad_ratio` | 路由阶段（PDFium 探测）；恢复后标记 |
| `layout` | `columns`、`has_table`、`is_complex`、`rotation` | 路由阶段 |
| `signals` | `char_density`、`glyph_coverage`、`empty_region_ratio`、`header_footer_ratio` | 解析完成后 |
| `quality` | `extract_quality`（页级 0–1）、`tier`（T0–T3） | 解析完成后（L1 后） |

**规则**：
- `route_reason` 原样落库，不裁剪 / 不映射 / 不丢弃；映射成缺陷码发生在 4.5.7 的配置表，不在落库时。
- `class_confidence` 是文档级分类把握，不可当页级质量分。
- **隐藏文本层复用**：扫描件若存在隐藏 OCR 文本层（PDFium 可读）且质量达标（字符数 ≥ 50、`U+FFFD` ≤ 1%、可打印率 ≥ 95%），`text_layer = invisible_recovered`，`route` 改为 `native`，`route_reason` 追加 `text_layer_recovered`；质量不达标才置 `needs_ocr`。恢复页的 `PageArtifact.warnings` 同时记录 `text_layer_recovered`，报告须声明来源。
- `glyph_coverage` 来自 `cmap_gaps`，**仅抽取后可得**（`detect_pdf` 阶段恒为空）→ 因此两段写入。
- 禁 `page_filter` 引用 `extract_quality`（因果循环）。

### PageArtifact（页产物）

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `doc_id` | **sha256** | 主键一部分 |
| `page_no` | int | 1-indexed |
| `engine` | `id@version` | 产生方（如 `engine.pdf@1.0.0`） |
| `params_hash` | sha256 | 参数指纹 |
| `content` | 结构 | `markdown` + `text_items[]`（text/bbox/font/size/rotation…）+ `tables[]`（cells 由 T1 补齐）+ `reading_order` |
| `scores` | 结构 | `class_confidence`（文档级继承）+ `extract_quality`（页级） |
| `warnings` | string[] | 警告 |
| `provenance` | 结构 | `input_hash`、`created_at`、`duration_ms`、`tier` |

**规则**：
- **不可变、内容寻址**：同一幂等键（内容哈希 + 页范围 + 引擎 + 参数 + 规则包版本）必须返回同一 artifact。
- `tables[].cells` 在插件原生产物中为空，由 T1 后处理填充；未走 T1 的页面该字段为空数组并置 warning，**不得伪造 cell 坐标**。
- 批完成即落盘释放，全本产物禁止常驻内存（合并阶段流式读取）。

### PageReport（质检报告）

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `doc_id` + `page_no` | 复合 | 定位 |
| `verdict` | 枚举 | `pass` / `fail` / `uncertain` |
| `extract_quality` | float | 页级 |
| `fidelity` | float | 四维还原度（需真值，仅验收 / 标定用） |
| `defects[]` | Defect 数组 | 缺陷清单（契约见 contracts/qc-output.md） |
| `attribution` | 封闭集 | 失败分类（4.5.2） |
| `recommended_action` | 枚举 | 建议动作 |
| `qc_confidence` | float | **QC 对自身判定的置信度**，不得与 `extract_quality` 混用 |

**规则**：QC 只读、不重新解析、不改产物；质检结果先落库再触发下游；`defects[]` 是本契约核心，`verdict` 只是结论。

### Defect（缺陷）

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `defect_id` | id | 标识 |
| `code` | **封闭集** | 11 个缺陷码（见 contracts/defect-codes.md），不得模型自由生成 |
| `location` | 结构 | **必填**：`page_no` +（`table_index` / `bbox` / `item_range` / `angle` 按码） |
| `evidence` | 结构 | 判定证据（指标值） |
| `severity` | float | 严重度 0–1 |
| `suggested_action` | 枚举 | 建议算子 |
| `raw_reason` | string | 仅 `code = plugin_reason_unknown` 时必填，上游原因原样保留 |

**规则**：**无 `location` 的缺陷不得入库、不进修复队列**，只能统计。码→算子映射写死在 `pipeline/repair/mapping.py`，不允许运行时推断。

### PageGap（缺口记录）

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `doc_id` + `page_no` | 复合 | 定位 |
| `defect_codes[]` | 封闭集数组 | 失败码 |
| `tried_operators[]` | 枚举数组 | 已尝试算子 |
| `recommended_tier` | T0–T3 | 建议档位 |
| `fallback_used` | 枚举 | 降级路径（local_ocr / mark_only / fail_doc） |

**规则**：由未修复的 Defect 聚合而来；进入复杂页收口流程（抽子集 → 聚包 → 页码回填）。

### DocReport（文档报告）

文档级聚合：`doc_id`、聚合指标（STP、gap 占比、档位分布、失败分布）、诊断。**交付等级**按 gap 页占比三档：`complete` / `partial_with_gaps` / `needs_review`。

### RulePack（规则包）

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `id` + `version` | 复合 | 标识 |
| `content` | YAML | 字段定义、`checks[]`（expression / sequence）、`thresholds` |
| `status` | 枚举 | 草稿 / 冻结 / 回滚 |

**规则**：纯数据；`type: expression` 用受限表达式引擎（白名单字段 / 函数，沙箱求值）；可版本化、可回滚、可 dry-run；每条规则可追溯原始需求文本。

### Action（修复动作）

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `id` | id | 标识 |
| `page` | 引用 | 目标页 |
| `type` | 枚举 | 动作（A–F、H、I，见 4.5.3） |
| `params` | 结构 | 参数 |
| `tier` | T0–T3 | 档位 |
| `result` | 枚举 | 结果 |
| **`changed`** | bool | **是否产生实际变化——停机依据**；`false` 即"此路不通"，不得重试同参数 |

### PluginRegistration（插件注册）

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `plugin_id` + `version` | 复合 | 标识（`id@version` 参与幂等键） |
| `kind` | 枚举 | engine / validator / extractor / connector / exporter |
| `provides[]` | 能力签名 | `capability` + 输入输出 schema + 延迟（契约见 contracts/plugin-manifest.md） |
| `determinism` | 枚举 | deterministic / stochastic / model |
| `trust` | 枚举 | builtin / signed / unsigned |
| `sandbox` | 枚举 | none / subprocess / container / remote |
| `enabled` | bool | 启停 |
| `installed_at` | ts | 安装时间 |

**规则**：禁"后加载覆盖先加载"（显式优先级决定）；禁插件间直接互调（必须经调度器）。

### Plan（任务级执行计划）【新增】

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `plan_version` | int | 版本；重规划产生新版本 |
| `stages[]` | 数组 | `processing` / `decision` / `replan` 三种 stage kind（禁止自由图结构） |
| `guards` | 结构 | `max_model_calls`（默认 8）、`max_replans`（默认 3）、`max_wall_time_s`（默认 3600） |
| `frozen_at` | ts | 冻结时间 |
| `created_by` | 枚举 | rule_template / model |

**规则**：冻结后不可改；重放历史任务用同一 `plan_version` 结果逐字一致；分支条件用受限表达式、白名单仅限统计指标。

### Decision（决策记录）【新增】

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `plan_version` | int | 所属 Plan 版本 |
| `stage_id` | str | 决策发生的 stage |
| `metric_snapshot` | 结构 | 求值时的统计指标快照 |
| `branch_taken` | str | 命中的分支 |

**规则**：每次 `decision` stage 求值写一条；可审计、可重放。

## 关系

```text
Task 1 — N Plan（重规划产生新 plan_version）1 — N Decision
Task 1 — N Document 1 — N PageProfile 1 — N PageArtifact
（同一页可有多个 artifact（不同引擎/参数），但同一幂等键只对应一个）

PageReport 1 — N Defect
PageGap 由未修复的 Defect 聚合而来
Document 1 — 1 DocReport
Task 1 — N Action（动作记录）
RulePack / PluginRegistration 独立于任务
```

## 状态机（唯一合法转移表）

| 状态 | 进入条件 | 动作 | 出口 |
| ---- | ---- | ---- | ---- |
| `RECEIVED` | 接入完成 | 完整性检测、建任务 | 通过→`SNIFFED`；失败→`REJECTED` |
| `REJECTED` | 边界处理未通过 | 写诊断码、入待确认队列 | 人工处理 |
| `SNIFFED` | 一级判定完成 | 按家族派发 | pdf→`CLASSIFIED`；其他→`ROUTED` |
| `CLASSIFIED` | `detect_pdf` 完成 | 写 PageProfile 前两段、逐页分配链路 | `ROUTED` |
| `ROUTED` | 链路确定 | 分批、入队 | `PARSING` |
| `PARSING` | worker 取到任务 | 批量解析 | `PARSED` |
| `PARSED` | artifact 落库 | 算 L1、写 PageProfile 后两段、判档位 | `POSTPROCESS`(T1) / `L1_CHECK`(T0) / `REPAIRING`(T2/T3) |
| `POSTPROCESS` | 档位 = T1 | 表格 cell 重建、页眉页脚剔除、跨页段重排 | `L1_CHECK` |
| `L1_CHECK` | 指标算完 | 硬门控 | pass→`L2_CHECK`；fail→`ATTRIBUTED` |
| `L2_CHECK` | 抽样命中 | 往返 / 跨引擎 / VLM | pass→`MERGED`；fail→`ATTRIBUTED` |
| `ATTRIBUTED` | 归因完成 | 选动作、判档 | T1→`POSTPROCESS`；T2/T3→`REPAIRING`；无法判→`ESCALATED` |
| `REPAIRING` | 动作确定且为 T2/T3 | 执行重跑 / 换引擎 | `PARSING`（同页/同批） |
| `MERGED` | 页级通过 | 组装 | `L3_CHECK` |
| `L3_CHECK` | 文档级校验 | 勾稽 / 连续性 | pass→`PUBLISHED`；fail→局部回退 |
| `PUBLISHED` | 输出完成 | 通知 | 终态 |
| `ESCALATED` | 预算耗尽 | 诊断卡 | 人工 |

**关键规则**：
- `POSTPROCESS` 是独立可观测状态（否则无法统计 T1 命中率与耗时）；**T1 动作改的是已有 artifact，绝不回到 `PARSING`**。
- 中断与续跑：worker 崩溃 → 任务状态不变、控制面重新入队、从 `resume_point` 之后继续；批级回滚（只有"产物已写对象存储 **且** 状态已提交"才算完成）；**状态机不新增恢复状态**——恢复 = 重新执行当前状态的动作，每个状态动作必须幂等。
- 禁止"状态已变 `PARSED` 但产物读不到"（写入顺序：先内容后索引）。

## 校验规则汇总

1. 主键：内容哈希（SHA-256），禁止自增 ID。
2. 页码：系统内一律 1-indexed；转换只在 `contracts/pages.py`。
3. 缺陷：`code` 封闭集 + `location` 必填；`raw_reason` 仅在 `plugin_reason_unknown` 必填。
4. 算子：幂等、不改原产物（返回新对象）。
5. Action：`changed == false` → 立即停机，不重试同参数。
6. PageProfile：两段写入，`route_reason` 原样保留。
7. TaskSpec：JSON Schema 校验 + 预览确认；`page_filter` 禁引用 `extract_quality`。
8. 档位 / 门控 / 路由判定：确定性规则，不由模型决定。
9. Plan：3 种 stage kind 白名单；分支表达式仅限统计指标；冻结后不可改；任务级模型调用 ≤ `max_model_calls`（默认 8）。
