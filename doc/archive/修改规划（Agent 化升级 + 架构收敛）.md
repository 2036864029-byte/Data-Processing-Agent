# 修改规划：Agent 化升级 + 架构收敛

**版本** V1.0 ｜ **日期** 2026-10-30 ｜ **状态** 待评审，尚未执行任何修改

**范围**：两件事一起改——(A) 引入 Agent 编排范式与 Pydantic AI 的受限引入；(B) 项目结构从 18 个平铺目录收敛为 5 个一级包。

**为什么必须一起改**：两者都要动同一批文件（`plan.md` 的结构树、`data-model.md` 的路径、`constitution.md` 的项目结构节）。分两次改等于把 22 行路径引用改两遍。

---

## 0. 前提核查

| 项 | 实测 |
|---|---|
| 代码 | **不存在**（无 `src/` 目录） |
| 有效文档 | `doc/PRD V2.2.md`（2601 行）、`.specify/memory/constitution.md`（191 行）、`specs/001-.../` 6 份共 1135 行 |
| `tasks.md` | **未生成**——这是最后窗口（tasks 由 plan 推导，plan 里没有的策略 tasks 不会有） |
| 分支 | 仅 `main`，5 次提交 |
| 路径引用点 | `extract/wrapper.py` 9 行 / `repair/mapping.py` 8 行 / `core·store·plugins·runtime` 5 行，共 22 行，分布在 **6 个文件**（含宪法） |

**结论**：改动发生在 markdown 层，成本最低；现在不改，M2 之后返工 ≥ 10 人日（8 个决策点会散落在 6–8 个文件里）。

---

# 第一部分 · Agent 化升级

## 1.1 目标形态

不做全自主 Agent（成本 ×延迟 × 不可复现，三条硬指标都不成立），做**任务级可重规划的 Agentic Workflow**：

```
┌ L1 会话层 · Agent 入口 ───────────────────────────────┐
│  自然语言 → Plan（含探路与分支）→ 下发 → 观察 → 重规划   │  模型：1 次 + 偏离时 ≤3 次
└────────────────────┬─────────────────────────────────┘
┌ L2 编排层 · 确定性图（9 状态，拓扑固定）───────────────┐
│  接入→体检→判定→分批→解析→质检→修复→合并→输出          │  零模型
└────────────────────┬─────────────────────────────────┘
┌ L3 决策层 · Policy 接口（8 个决策点）──────────────────┐
│  页级 5 个 = 规则写死 ｜ 任务级 3 个 = 可为模型          │  可替换实现
└──────────────────────────────────────────────────────┘
```

## 1.2 最关键的一条设计约束（决定可复现性能否保住）

> **模型只负责「生成计划」，不负责「执行时判断」。**

- Plan 由模型生成**一次** → 冻结落库 → 执行严格按 Plan 走 → 执行路径确定、产物可复现
- 分支条件不是模型自由判断，是**复用 `rules/` 的 json-logic 受限表达式**，对指标求值（`stp >= 0.85`）
- 重放历史任务时用同一 `plan_version` 序列，结果逐字一致
- 重规划是例外路径：每次产生新 `plan_version`，落库、可 diff、计入 `max_replans`，超出即停止并告警

这条区分让"引入 Agent"与"确定性优先"不再冲突：**灵活性被限制在计划生成时刻，而不是执行时刻。**

## 1.3 Plan 对象（TaskSpec 之上新增）

`TaskSpec` 现在是**参数**（`stp_target`、`budget`），不含计划。新增 `plan` 段，只有 3 种 stage kind：

```yaml
plan:
  version: 1
  stages:
    - id: probe                 # 探路：先跑 100 页
      kind: processing
      scope: { pages: "1-100" }
      strategy: quality
    - id: decide                # 分支：读探路统计
      kind: decision
      on: { metric: stp_rate, window: probe }
      branches:
        - when: "stp_rate >= 0.85"   # json-logic，白名单仅限统计指标
          goto: bulk_fast
        - when: "stp_rate < 0.75"
          goto: bulk_quality
        - else: true
          goto: bulk_balanced
    - id: bulk_fast
      kind: processing
      scope: { pages: "101-" }
      strategy: fast
  guards:
    max_model_calls: 8          # 硬上限，超出即拒绝该 Plan
    max_replans: 3
    max_wall_time_s: 3600
```

**三种 stage kind 就是全部**：`processing`（处理一段）、`decision`（对指标求值分支）、`replan`（请求重规划）。禁止自由图结构——否则 L2 就从确定性图退化成解释器。

## 1.4 八个决策点

| # | 决策点 | 位置 | P0 实现 | 未来 | 是否参与指标 |
|---|---|---|---|---|---|
| 1 | `PlanPolicy` 自然语言 → Plan | L1 | 模型 | 模型 | 否 |
| 2 | `ReplanPolicy` 偏离 → 新 Plan | L1 | 规则（阈值触发） | 模型 | 否 |
| 3 | `StrategyPolicy` 整本转 OCR / 降档 / 中止 | L1→L2 | 规则 | 模型 | 否 |
| 4 | `RoutePolicy` 这页去哪条链路 | L2 节点内 | 规则 | **保持规则** | **是** |
| 5 | `TierPolicy` 走 T0–T3 哪档 | L2 节点内 | 规则 | **保持规则** | **是** |
| 6 | `OperatorPolicy` 选哪个算子 | L2 节点内 | 规则（mapping 表） | **保持规则** | **是** |
| 7 | `AttemptPolicy` 还要不要再试一轮 | L2 节点内 | 规则（三层停机） | **保持规则** | **是** |
| 8 | `EscalationPolicy` 何时找人 | L2 节点内 | 规则（预算耗尽） | 保持规则 | 是 |

**分界线一句话**：页级决策参与 STP 计算，必须规则、必须可复现；任务级决策每任务只发生几次，可以用模型且不影响指标。

## 1.5 Pydantic AI 的引入边界

| 项 | 决定 |
|---|---|
| 用在哪 | **仅** `kernel/policies/model_*.py`（3 个模型实现的内部） |
| 不能用在 | `pipeline/` 任何位置、L2 编排、页级决策 |
| 提供什么 | 结构化输出校验、类型安全、失败重试、调用计量 |
| 不提供什么 | 不接管编排（不用 `pydantic_graph`）、不接管状态、不接管续跑 |
| 版本策略 | 锁精确版本进 `uv.lock`；离线包需含其依赖 |
| CI 门禁 | `grep -rn "pydantic_ai" src/ \| grep -v "kernel/policies/model_"` 非空即失败 |

**为什么不用它编排**：它的 durable execution 与既有续跑设计（`resume_point` + 批级事务 + `outbox`）是两套状态源，只能二选一；选框架等于推翻 FR-020 与 SC-010。

## 1.6 第一部分改动清单

| # | 文件 | 位置 | 动作 | 内容要点 | 工时 |
|---|---|---|---|---|---|
| A1 | `contracts/policies.md` | 新建 | 新增 | 8 个 Protocol 签名 + 页级/任务级分界表 + 实现约束（必须无状态、必须可注入假实现） | 0.3d |
| A2 | `contracts/taskspec.md` | 第 5 节前 | 新增 | `plan` 段、3 种 stage kind、`guards` 三项、3 条校验规则（分支表达式白名单、`max_model_calls` 上限、Plan 冻结后不可改） | 0.3d |
| A3 | `spec.md` | FR 末尾 | 新增 | `FR-026` 任务级规划（NL→Plan，Plan 冻结后执行确定性，重规划上限）、`FR-027` 决策点策略化（页级规则不可替换为模型） | 0.2d |
| A4 | `spec.md` | SC 末尾 | 新增 | `SC-014` 同 `plan_version` 重放结果逐字一致；任务级模型调用 ≤ 8 次/任务 | 0.1d |
| A5 | `spec.md` | Edge Cases | 新增 2 条 | 预算在第 3 次重规划后仍不达标 → 停止并交付带缺口结果；Plan 分支条件引用未知指标 → 生成期拒绝 | 0.1d |
| A6 | `plan.md` | Summary（第 11 行） | 改写 | "三个模型调用点" → "页级规则 + 任务级模型（≤3 处，每任务 ≤8 次调用）" | 0.1d |
| A7 | `plan.md` | Constitution Check G1 | 改写 | 遵守方式补：页级决策走 Policy 规则实现，任务级走 Policy 模型实现，二者接口分离 | 0.1d |
| A8 | `plan.md` | P0-A 表 | 新增 A8 行 | `决策点策略化：policies/ 接口 + 规则实现 + 配置开关` | 0.1d |
| A9 | `data-model.md` | 实体一节 | 新增 2 实体 | `Plan`（version, stages, guards, frozen_at, created_by）／`Decision`（plan_version, stage_id, metric_snapshot, branch_taken） | 0.2d |
| A10 | `research.md` | Decision 一节 | 新增 2 条 | ①为什么不用 LangGraph / pydantic_graph 编排（续跑冲突为首要理由）②Pydantic AI 边界 | 0.2d |
| A11 | `quickstart.md` | 场景 5 后 | 新增 1 场景 | 自然语言 → Plan 预览 → 探路 100 页 → 分支 → 输出（验证 Plan 落库与重放一致） | 0.1d |
| A12 | `constitution.md` | 原则 I | 改写 | 见 1.7 | 0.2d |
| A13 | `constitution.md` | 技术栈表 | 新增 1 行 | `pydantic-ai`，备注"仅 kernel/policies/model_*" | 0.1d |

## 1.7 宪法原则 I 的具体改法

**原文**：「整条流水线除三个模型调用点（意图→TaskSpec、失败归因、修复动作兜底）外，必须全部是确定性代码」

**改为**：

> - 决策分两级：**页级决策一律规则**（路由、档位、算子选择、是否续试、何时升级），**不得**由模型决定，且必须可复现；
> - **任务级决策**（自然语言→Plan、偏离重规划、整本策略切换）可由模型承担，但**每任务调用 ≤ 8 次**，且 Plan 一经冻结，执行路径必须确定；
> - Plan 的分支条件必须是受限表达式（json-logic），白名单仅限统计指标，**禁止**自由图结构与运行时解释器。

**理由补一句**：灵活性只允许存在于"计划生成时刻"，不允许存在于"执行时刻"——否则 STP 率不可复现，验收失效。

版本：`1.0.0 → 1.1.0`（MINOR：实质扩展原则 I，并新增技术栈条目）。

---

# 第二部分 · 架构收敛

## 2.1 现状问题（不是"平铺"，是三维混层）

| 性质 | 现有目录 | 问题 |
|---|---|---|
| 处理阶段 | ingest / sniff / route / profile / extract / quality / repair / merge / output | 有先后与回路 |
| 支撑设施 | store / runtime / observe / rules / plugins | 被所有阶段调用 |
| 全局契约 | core（状态机+ID+错误码+预算）、`extract/wrapper.py`、`repair/mapping.py` | 全局唯一，被多方依赖 |
| 入口外壳 | cli / api | 只调不被调 |

**实质错误只有一个**：`extract/wrapper.py`（页码基准）与 `repair/mapping.py`（缺陷码→算子）是全工程共享的契约，却被放在两个业务阶段目录里，导致 route / quality / merge 要反向依赖 `extract`。这不是审美问题，是依赖方向错乱。

**另一个隐性问题**：`core/` 把状态机（业务规则，常变）和错误码/ID（契约，几乎不变）装在同一个目录，变更频率差一个数量级。

## 2.2 目标结构（5 个一级包）

```
src/dataagent/
├── contracts/                 # 契约层：只定义不实现，依赖 = 无
│   ├── ids.py                 #   内容哈希 / uuidv7（原 core）
│   ├── errors.py              #   错误码封闭集（原 core）
│   ├── pages.py               #   ★ 唯一页码转换点（原 extract/wrapper.py）
│   ├── defects.py             #   缺陷码枚举 + Defect / OperatorResult（原 repair 内定义）
│   ├── plugin.py              #   插件 Protocol + manifest schema（原 plugins/protocol.py + manifest.py）
│   └── policies.py            #   8 个决策点 Protocol【新增】
├── platform/                  # 支撑设施：依赖 = contracts
│   ├── config.py              #   单一配置 schema（原 src/dataagent/config.py）
│   ├── store/                 #   对象存储 abstract（local + s3）+ 队列 + SQL 子集
│   ├── runtime/               #   worker / supervisor / heartbeat / resources / sandbox
│   ├── observe/               #   span / 指标 / 日志 / 审计
│   ├── rules/                 #   RulePack + json-logic 表达式引擎
│   └── plugins/               #   loader + adapters/（engine_pdf / engine_csv / engine_text）
├── kernel/                    # 内核：依赖 = contracts + platform
│   ├── state.py               #   状态机唯一转移表（原 core）
│   ├── budget.py              #   三层预算（原 core）
│   ├── plan.py                #   Plan 解析 / 冻结 / 分支求值【新增】
│   ├── registry.py            #   插件注册表（原 plugins/registry.py）
│   └── policies/              #   Policy 实现：rule_*.py（P0）/ model_*.py（M3，唯一可用 pydantic_ai）
├── pipeline/                  # 处理阶段：依赖 = contracts + platform + kernel
│   ├── ingest/  sniff/  route/  profile/  extract/
│   ├── quality/  repair/  merge/  output/
└── apps/                      # 入口：依赖 = 全部
    ├── cli/  api/

migrations/  tests/  deploy/  docs/     # 保持包外（现状正确，不动）
```

一级目录 **18 → 5**，最深层级 `dataagent/pipeline/extract/artifacts.py`（3 层目录 + 文件），不比 `django/contrib/admin/` 深。

**一处刻意保留**：`repair/mapping.py`（缺陷码→算子）**不**上提到 `contracts/`。区别在于——`defects.py` 是被 quality 产出、repair 消费、output 报告的**多方依赖定义**，属于契约；`mapping.py` 只被 repair 自己用的**单方规则**，留在原地。判据是"被几方依赖"，不是"重不重要"。

## 2.3 依赖方向（可执行，不靠自觉）

```
apps  →  pipeline  →  kernel  →  platform  →  contracts
```

`contracts` 不 import 任何内部模块；反向依赖一律禁止。写进 CI：

```toml
[[tool.importlinter.contracts]]
name = "分层单向"
type = "layers"
layers = ["dataagent.apps", "dataagent.pipeline", "dataagent.kernel",
          "dataagent.platform", "dataagent.contracts"]
```

同时保留三条既有铁律的检查（页码唯一转换点 → 改为 `contracts/pages.py`；禁 `asyncio.run`；禁直连 `redis` / `boto3`）。

## 2.4 旧路径 → 新路径映射（共 20 条）

| 旧 | 新 | 说明 |
|---|---|---|
| `config.py` | `platform/config.py` | 横切配置 |
| `core/`（状态机、预算） | `kernel/state.py`、`kernel/budget.py` | 业务规则 |
| `core/`（ids、errors） | `contracts/ids.py`、`contracts/errors.py` | 契约 |
| `extract/wrapper.py` | **`contracts/pages.py`** | ★ 页码基准，全工程唯一 |
| `extract/`（其余） | `pipeline/extract/` | 分批、artifacts 编解码 |
| `repair/mapping.py` | `pipeline/repair/mapping.py` | 原地不动（只改名路径前缀） |
| `repair/`（其余） | `pipeline/repair/` | 算子库、增强回路 |
| `ingest/ sniff/ route/ profile/` | `pipeline/*` | — |
| `quality/ merge/ output/` | `pipeline/*` | — |
| `plugins/protocol.py` | `contracts/plugin.py` | 契约 |
| `plugins/manifest.py` | `contracts/plugin.py` | 契约 |
| `plugins/registry.py` | `kernel/registry.py` | 决策/元数据 |
| `plugins/loader.py` | `platform/plugins/loader.py` | 设施 |
| `plugins/adapters/` | `platform/plugins/adapters/` | 内置实现 |
| `store/` | `platform/store/` | — |
| `runtime/` | `platform/runtime/` | — |
| `observe/` | `platform/observe/` | — |
| `rules/` | `platform/rules/` | — |
| `cli/` `api/` | `apps/cli/` `apps/api/` | — |
| （无） | `kernel/policies/`、`kernel/plan.py`、`contracts/policies.py` | 新增 |

## 2.5 第二部分改动清单

| # | 文件 | 位置 | 动作 | 工时 |
|---|---|---|---|---|
| B1 | `plan.md` | `Project Structure` 结构树（118–145 行） | 整段替换为 5 层树 | 0.2d |
| B2 | `plan.md` | `Structure Decision`（147 行） | 改为依赖方向 + import-linter 表述 | 0.1d |
| B3 | `plan.md` | P0-A 表 A2/A4/A5/A6 落点列 | 路径改新值 | 0.1d |
| B4 | `plan.md` | G9 门禁行 | `extract/wrapper.py` → `contracts/pages.py` | 0.05d |
| B5 | `constitution.md` | 项目结构节（102–132 行） | 整段替换 + 新增"依赖方向可执行"一句 | 0.2d |
| B6 | `constitution.md` | 原则 II（35 行） | 两处路径改写 | 0.05d |
| B7 | `constitution.md` | 工程门禁（页码 grep 行） | 路径改写，并修掉不可执行的 `grep +1 / -1` → 改为按变量名匹配 | 0.05d |
| B8 | `data-model.md` | 196 行、99 行 | 两处路径改写 | 0.05d |
| B9 | `contracts/plugin-manifest.md` | 81 行 | 路径改写 | 0.02d |
| B10 | `contracts/defect-codes.md` | 24、33 行 | 路径改写 | 0.02d |
| B11 | `research.md` | 29 行 | 路径改写 | 0.02d |
| B12 | `quickstart.md` | 全文 | 检查是否有旧路径 | 0.05d |

---

# 第三部分 · 执行顺序与决策

## 3.1 建议顺序（可一次提交，也可分两次）

```
1. constitution.md          原则 I + 项目结构 + 门禁 + 版本 1.1.0（含 Sync Impact Report）
2. contracts/policies.md    新建（8 个 Protocol）
3. contracts/taskspec.md    加 plan
4. contracts/plugin-manifest.md、defect-codes.md   路径改写
5. spec.md                  FR-026/027、SC-014、Edge Cases
6. plan.md                  Summary、A8 行、结构树、G1/G9、Structure Decision
7. data-model.md            Plan / Decision 实体 + 路径
8. research.md              +2 条决策 + 路径
9. quickstart.md            +1 场景 + 路径
10. 复核：全仓 grep 旧路径，必须为 0
11. 然后执行 /speckit-tasks
```

**第 11 步之前必须做完第 10 步**——否则生成出来的 tasks 会带旧路径。

## 3.2 三个待你拍板的决策点

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| **D1** | PRD V2.2 是否升 V2.3 | A：不动，只在 specs 层改（快，但 PRD 与 specs 说法不一致）<br>B：升 V2.3，只改 3 处（2.1/2.2 模型调用点、4.1.4 TaskSpec、新增 4.1.9） | **B**。宪法写明"PRD 是唯一需求依据"，两处说法不一致以后没人说得清。成本 0.3d |
| **D2** | 结构收敛是否本次一次做完 | A：本次只做 Agent 化，结构下次<br>B：一次做完 | **B**。路径引用 22 行分布在 6 个文件，分两次=改 44 行，且中间态会误导 tasks 生成 |
| **D3** | `StrategyPolicy` / `ReplanPolicy` 的模型实现何时做 | A：P0 就实现<br>B：M1 只留接口，M3 实现 | **B**。P0 用规则阈值触发（40% 页失败→整本转 OCR）已足够，模型实现要等探索期有真实统计数据 |

## 3.3 风险与回退

| # | 风险 | 应对 |
|---|---|---|
| R1 | 文档改多后 PRD 与 specs 说法不一致 | 确立单向引用：PRD 管"要什么"，specs 管"实现成什么样"；PRD 变更必须回写 specs，反向不要求 |
| R2 | 收敛后旧路径在讨论记录/记忆里失效 | 2.4 的映射表留档，作为唯一对照 |
| R3 | 有了 Plan 之后有人真去实现"自由分支" | 契约层限死 3 种 stage kind + 表达式白名单；`kernel/plan.py` 拒绝未知 kind |
| R4 | Pydantic AI 升级破坏模型调用点 | 锁精确版本；只有 3 个文件引用；失败可回退为裸 HTTP 调用（Protocol 不变） |
| R5 | 团队误以为"引入 Agent"= 可以放松确定性 | 宪法原则 I 改写后明确"执行时刻必须确定"，并加 `SC-014` 重放一致性验收 |

## 3.4 验收（怎么算改完）

- [ ] 全仓 `grep -r "extract/wrapper.py"` = 0；`grep -r "\bcore/"` = 0（除 PRD 历史章节）
- [ ] `contracts/policies.md` 存在，8 个 Protocol 各有签名与约束
- [ ] `taskspec.md` 含 `plan` 段与 3 条校验规则
- [ ] `spec.md` 新增 FR-026 / FR-027 / SC-014
- [ ] `plan.md` 结构树为 5 层；G1 体现页级/任务级二分
- [ ] `data-model.md` 含 `Plan` / `Decision`
- [ ] 宪法版本 1.1.0，原则 I 已改写，Sync Impact Report 在档
- [ ] `plan.md` 的 `Structure Decision` 指到 import-linter 而非文字约定

## 3.5 工时汇总

| 部分 | 工时 |
|---|---|
| 第一部分（Agent 化，A1–A13） | 1.9 人日 |
| 第二部分（结构收敛，B1–B12） | 0.6 人日 |
| D1 若采纳（PRD V2.3） | 0.3 人日 |
| 复核与一致性检查 | 0.2 人日 |
| **合计** | **约 3 人日** |

对应收益：省掉 M2 之后 ≥10 人日的返工；且 tasks 生成时即可带上策略化接缝与正确分层。

---

**（本文件为规划，未修改任何既有文档）**
