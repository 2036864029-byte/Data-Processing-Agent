---

description: "P0-A 基础支撑层任务清单（A1–A9 + 集成门禁，对应里程碑 M1）"
---

# Tasks: 插件化异构数据处理 Agent（DataAgent）— P0-A 基础支撑层

**Input**: Design documents from `/specs/001-heterogeneous-data-agent/`

**Prerequisites**: plan.md（执行优先级 A1–A9）、spec.md、research.md（D3/D10/D11 决策）、data-model.md（13 实体 + 状态机 + 主键铁律）、contracts/（7 份契约）、宪法 1.2.0（`.specify/memory/constitution.md`，5 层包结构 + G1–G10 门禁）

**Tests**: 本项目宪法 G8 强制 TDD（测试先行、核心模块覆盖率 ≥ 90%），故每个模块 Phase 均含**先红后绿**的测试任务。

**Organization**: 按 plan「执行优先级与里程碑」组织——P0-A 基础支撑层（A1–A9）每一模块为一个独立可测 Phase；P0-B 功能模块层（B1–B9）**不在此文件**，见 `tasks-p0-b.md`（任务编号承接本文自 T073 起，两份文件合为**完整 P0 任务图**，均在实现开始前完成）。

**Scope 说明**：A1–A9 是串行依赖链（A1 底座 → A2 核心 → A3 共享 → A4 存储 → A5 注册表 → A6 续跑 → A7 交付骨架 → A8 策略化 → A9 可观测），本文件按此顺序执行；模块内标记 [P] 的任务可并行。

## Format: `[ID] [P?] [Module] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Module]**: 所属 A 模块（A1–A9），用于可追溯性；Setup 与集成收尾阶段无标签
- 描述含精确文件路径

## 路径约定

- 单一 src-layout：`src/dataagent/`，5 层包 `contracts → platform → kernel → pipeline → apps`（依赖方向单向，import-linter `layers` 契约强制）
- `tests/`：`unit` / `integration` / `e2e` / `fault` / `bench`
- `migrations/`（Alembic）、`deploy/`（Compose / Dockerfile / offline）、`docs/`（ADR + CHANGELOG）

---

## Phase 1: Setup — A1 工程底座

**Purpose**: 项目初始化、uv 唯一包管理、5 层目录骨架、CI 门禁、TDD 基建。**一切模块的依赖。**

**Independent Test**: 全新机器 `uv sync` 一条命令通过；CI 四件套（ruff lint / mypy / pytest / import-linter）全绿；`uv.lock` 存在并随源码提交（G7）；门禁脚本空仓库不误报。

- [ ] T001 创建单一 src-layout 5 层包骨架：`src/dataagent/{contracts,platform,kernel,pipeline,apps}/`（各含 `__init__.py`）、`tests/{unit,integration,e2e,fault,bench}/`、`migrations/`、`deploy/`、`docs/`；根目录 `pyproject.toml`（Python 3.12，项目名 `dataagent`）
- [ ] T002 [P] 用 uv 添加依赖并锁定：`uv add pydantic pydantic-settings json-logic sqlalchemy alembic pytest hypothesis pytest-cov testcontainers` 及 dev 工具 `ruff mypy import-linter`；`uv lock` 生成 `uv.lock` 随源码提交（G7：禁 pip / poetry / conda，新增依赖一律 `uv add`）
- [ ] T003 [P] 配置 `pyproject.toml` 工具段：ruff / mypy（`kernel/` `platform/store/` `pipeline/repair/` 严格）/ pytest-cov（核心模块 ≥ 90%、其余 ≥ 80%）/ import-linter `layers` 契约——`layers = ["dataagent.apps", "dataagent.pipeline", "dataagent.kernel", "dataagent.platform", "dataagent.contracts"]`（反向依赖即失败，G9）
- [ ] T004 [P] 建立 CI 门禁 `.github/workflows/ci.yml`：lint / mypy / pytest / import-linter / `uv lock --check` / 门禁脚本
- [ ] T005 [P] 建立门禁脚本 `scripts/check-gates.sh`：grep 校验（违反即失败）——页码转换符号（`page_from_plugin`/`page_to_internal`）仅出现在 `src/dataagent/contracts/pages.py`、`apps/worker` 内禁 `asyncio.run`、业务代码禁直接 `redis.xadd`/`boto3.client`、算子禁 IO（`open`/`requests`/db）、`pydantic_ai` 仅 `src/dataagent/kernel/policies/model_*.py`、环境脚本无 `pip install`/`poetry`/`conda`（G9）
- [ ] T006 建立 TDD 基建：`tests/conftest.py` + 夹具（testcontainers 起 PG / Redis / MinIO 的集成夹具、临时目录 fixture、确定性随机种子）、pytest-cov 分模块阈值配置（宪法「测试分层与目标」：单元行覆盖 ≥ 80%、核心模块 ≥ 90%）

**Checkpoint**: `uv sync` + CI 四件套全绿 → `git add . && git commit && git push`（每模块即推送约定）

---

## Phase 2: A2 核心机制

**Goal**: 单一配置 schema、状态机唯一合法转移表、内容哈希 / uuidv7、错误码封闭集、三层预算。**一切模块依赖。**

**Independent Test**: 单元全绿；状态机转移表每条合法转移有唯一后继、非法转移抛错；错误码封闭枚举穷尽；内容哈希幂等（同内容同哈希）。

### 测试（TDD：先红后绿，宪法 G8 强制）⚠️

> **NOTE: 以下测试先写、确保先 FAIL 再实现。**

- [X] T007 [A2] 状态机转移表测试 `tests/unit/kernel/test_state.py` — data-model.md「唯一合法转移表」：`RECEIVED→SNIFFED/REJECTED`、`PARSED→POSTPROCESS/L1_CHECK/REPAIRING`、`L1_CHECK→L2_CHECK/ATTRIBUTED`、`ATTRIBUTED→POSTPROCESS/REPAIRING/ESCALATED`、`L3_CHECK→PUBLISHED` 等全部合法转移断言唯一后继；非法转移（如 `SNIFFED→PUBLISHED`）抛错；恢复语义 = 重新执行当前状态动作、每个状态动作幂等
- [X] T008 [P] [A2] 内容哈希 / uuidv7 测试 `tests/unit/contracts/test_ids.py` — SHA-256 幂等（同内容同哈希、不同内容不同哈希）；uuidv7 单调可排序（时间前缀递增）
- [X] T009 [P] [A2] 错误码封闭集测试 `tests/unit/contracts/test_errors.py` — 枚举穷尽、无运行时字符串字面量、新增码必须走版本升级（宪法「缺陷码是封闭集」）
- [X] T010 [A2] 三层预算测试 `tests/unit/kernel/test_budget.py` — 成本 / 次数 / 耗时三层扣减；耗尽触发升级（budget 结构见 data-model.md Task.budget）
- [X] T011 [A2] 单一配置 schema 测试 `tests/unit/platform/test_config.py` — pydantic-settings 从 env 与 config.yaml 载入同一 schema；单机多机同构（G10：配置只有一份 schema，差别只是值）

### 实现 A2

- [X] T012 [P] [A2] 实现 `src/dataagent/contracts/ids.py` — 内容哈希（SHA-256）与 uuidv7；幂等键 = `hash(文件) + 页范围 + 引擎 + 参数 + 规则包版本`（主键铁律：**文档与页产物主键必须是内容哈希，不得使用自增 ID**）
- [X] T013 [P] [A2] 实现 `src/dataagent/contracts/errors.py` — 错误码封闭集枚举（无内部依赖叶子模块）
- [X] T014 [P] [A2] 实现 `src/dataagent/kernel/state.py` — 状态机唯一合法转移表（纯映射、无副作用；含 REJECTED / ESCALATED 终态；不新增恢复状态）
- [X] T015 [P] [A2] 实现 `src/dataagent/kernel/budget.py` — 三层预算（成本 / 次数 / 耗时）扣减与耗尽判定；档位成本扣减规则（FR-008）：后处理 0 次、参数重跑 1 次、换引擎 2 次
- [X] T016 [A2] 实现 `src/dataagent/platform/config.py` — 单一配置 schema（pydantic-settings）

**Checkpoint**: A2 单测全绿 → commit + push

---

## Phase 3: A3 全局共享机制

**Goal**: 页码基准转换点（全工程仅一处）+ 缺陷码封闭枚举 + 缺陷码→算子映射写死。**路由 / 质检 / 修复依赖。**

**Independent Test**: 页码转换 round-trip 断言通过（回归必写测试 #1）；grep 门禁校验转换符号仅出现在 `contracts/pages.py`；`mapping.py` 覆盖全部 11 个缺陷码、枚举与映射长度一致。

### 测试（TDD：先红后绿）⚠️

- [ ] T017 [A3] 页码基准测试 `tests/unit/contracts/test_pages.py` — 断言式单测固定 1-indexed：`extract_pages_markdown(path, pages=[k])` 后 `pages[0].page == k`，且该页有表时 `k+1 in pages_with_tables`（必写 #1）；`page_from_plugin` / `page_to_internal` round-trip；边界（第 1 页合法、第 0 页报错、末页）
- [ ] T018 [A3] 缺陷码枚举 + 映射测试 `tests/unit/contracts/test_defects.py` 与 `tests/unit/pipeline/repair/test_mapping.py` — 11 码封闭枚举（逐字引用 defect-codes.md）；`pipeline/repair/mapping.py` 覆盖全部 11 码且枚举与映射表长度一致（G9 grep 门禁）；`location` 必填、缺 location 不进修复队列；`raw_reason` 仅 `code = plugin_reason_unknown` 时必填

### 实现 A3

- [ ] T019 [A3] 实现 `src/dataagent/contracts/pages.py` — **★ 唯一页码转换点**：`page_from_plugin` / `page_to_internal`，内部一律 1-indexed（转换符号变量名固定，供 grep 门禁匹配）
- [ ] T020 [P] [A3] 实现 `src/dataagent/contracts/defects.py` — 缺陷码封闭枚举（11 项，含默认算子与档位，见 defect-codes.md）+ `Defect` / `OperatorResult` 类型（`location` 必填、`changed` 停机标志）
- [ ] T021 [A3] 实现 `src/dataagent/pipeline/repair/mapping.py` — `defect_code → operator` 映射**写死在代码里**（按 defect-codes.md「默认算子」列），单向、禁运行时推断（同一缺陷两次修复不得走不同算子）

**Checkpoint**: 页码 / 映射单测 + grep 门禁全绿 → commit + push

---

## Phase 4: A4 存储抽象

**Goal**: 对象存储双实现（本地盘 + S3）、队列最小语义（至少一次 + 手动确认 + 死信）、SQL 标准子集 + Alembic 迁移。**接入 / 解析 / 续跑依赖。**

**Independent Test**: 同一套对象存储测试套件对 local 与 MinIO（testcontainers）均通过；队列测试过 Redis；`alembic upgrade head` 在空库上干净执行、`downgrade base` 可回滚。

### 测试（TDD：先红后绿）⚠️

- [ ] T022 [A4] 对象存储契约测试 `tests/integration/store/test_objectstore.py` — put / get / exists / delete / list；内容哈希 key 换底座不变；**同一套套件参数化**分别跑 local 与 MinIO（宪法：业务只经 `platform/store/objectstore/`，禁直接 `boto3.client`）
- [ ] T023 [A4] 队列最小语义测试 `tests/integration/store/test_queue.py` — 至少一次 + 手动确认 + 死信；**不依赖延迟消息与优先级队列**（应用层用 `visible_at` 实现，research D6）；testcontainers Redis
- [ ] T024 [A4] SQL 子集 + Alembic 测试 `tests/integration/store/test_sql.py` — `alembic upgrade head` / `downgrade base` 干净；所有 DDL 走迁移（G9 grep 门禁：migrations 与 models 一致）；SQLAlchemy Core 无 ORM

### 实现 A4

- [ ] T025 [A4] 实现 `src/dataagent/platform/store/objectstore/base.py` — 抽象接口（put / get / exists / delete / list）+ 内容寻址 key 约定（主键 = SHA-256 内容哈希）
- [ ] T026 [P] [A4] 实现 `src/dataagent/platform/store/objectstore/local.py` — 本地盘实现（原子写、先写内容再写索引、临时盘预留）
- [ ] T027 [A4] 实现 `src/dataagent/platform/store/objectstore/s3.py` — S3 实现（`boto3.client` 只允许出现在此文件，业务代码禁止直接调用，G9）
- [ ] T028 [A4] 实现 `src/dataagent/platform/store/queue/redis.py` — Redis Streams 最小语义（XADD / XREADGROUP / XACK / 死信，全部封装，业务禁直接 `redis.xadd`，G9）
- [ ] T029 [P] [A4] 实现 `src/dataagent/platform/store/sql/` — SQLAlchemy Core（禁 ORM）引擎 / 连接工厂，标准 SQL 子集，P0 不做运行时切换
- [ ] T030 [A4] 初始化 Alembic 迁移骨架 `migrations/` — `alembic init` + `env.py` + 基线空迁移（后续所有 DDL 变更必须走迁移）

**Checkpoint**: 双实现同一套件全绿、迁移干净 → commit + push

---

## Phase 5: A5 插件注册表与加载器

**Goal**: 能力索引 / 启停 / 版本锁定 / 依赖与兼容性校验 / 多实现显式优先级；热路径同进程直接调用、冷路径子进程沙箱；**禁后加载覆盖先加载、禁插件间直接互调**。**路由 / 解析 / 质检依赖。**

**Independent Test**: 注册 engine.pdf manifest（直读 manifest，不加载真实引擎）后按能力索引命中 `pdf.classify` / `pdf.text.extract`；同能力双实现按显式优先级选择；重复加载被拒；插件间无互调通道。

### 测试（TDD：先红后绿）⚠️

- [ ] T031 [A5] manifest schema 校验测试 `tests/unit/contracts/test_plugin.py` — 合法 engine.pdf manifest（plugin-manifest.md 示例）通过；缺 `determinism`、`trust: unsigned` 但 `sandbox: none` 拒绝（验收 1–5）；`provides` 命名 `域.动作`、`*.classify` 与 `*.extract` 不交叉
- [ ] T032 [A5] 注册表行为测试 `tests/unit/kernel/test_registry.py` — 能力索引 / 启停 / 版本锁定 / 依赖兼容校验 / 同能力多实现显式优先级 / 后加载覆盖被拒（G3）；`determinism` 缺失或 `stochastic / model` 禁启用结果缓存

### 实现 A5

- [ ] T033 [A5] 实现 `src/dataagent/contracts/plugin.py` — 插件 Protocol + manifest schema（api_version 校验、provides 能力签名、`determinism` / `trust` / `sandbox` / `batching` / `compatibility`，逐字引用 plugin-manifest.md）
- [ ] T034 [P] [A5] 实现 `src/dataagent/platform/plugins/loader.py` — 从插件目录读 manifest 加载；热路径插件同进程直接调用；冷路径子进程沙箱（调用 sandbox.py）
- [ ] T035 [A5] 实现 `src/dataagent/platform/runtime/sandbox.py` — 冷路径子进程沙箱执行设施（超时 / 资源上限 / 隔离）※ 归 A5（加载器冷路径需要），A6 续用
- [ ] T036 [A5] 实现 `src/dataagent/kernel/registry.py` — 插件注册表：能力索引（capability→plugin）、启停、版本锁定、依赖 / 兼容性校验、显式优先级、禁后加载覆盖、禁插件间互调（必须经调度器）

**Checkpoint**: 注册 / 索引 / 优先级行为全绿 → commit + push

---

## Phase 6: A6 续跑与资源预检

**Goal**: 落盘分层、批级事务边界、进程池隔离、心跳按进展判定、启动自检 + 建议值 + OOM 降批链、supervisor 状态机驱动调度。**全部长任务依赖。**

**Independent Test**: 故障注入（进程内模拟强杀）后任务从 `resume_point` 继续、已完成批 0 重做（SC-010）；转移表全合法；资源建议值按公式（`min(核数×1.25, 可用内存/单进程上限, 32)`，**禁写死 `cpu_count()`**）。

### 测试（TDD：先红后绿）⚠️

- [ ] T037 [A6] supervisor 状态机 / 续跑测试 `tests/unit/kernel/test_supervisor.py` — 任务沿唯一转移表推进；**恢复从 resume_point 后的批继续、已完成批 0 重做**；批级事务边界（产物 + 状态都成功才完成，先写内容再写索引，G6）
- [ ] T038 [A6] 心跳按进展测试 `tests/unit/platform/runtime/test_heartbeat.py` — 连续 60s 无心跳判死；按「进展」而非「耗时」判定（research D7）
- [ ] T039 [A6] 资源预检测试 `tests/unit/platform/runtime/test_resources.py` — 建议值公式 `min(核数×1.25, 可用内存/单进程上限, 32)`（禁写死 `cpu_count()`，宪法「性能与稳定性预算」）；用户覆盖后超限告警并降级；OOM 降批链 32→16→8、降到最低仍失败则停止并告警
- [ ] T040 [A6] 进程池隔离测试 `tests/unit/platform/runtime/test_pool.py` — CPU 密集路径用**进程池**（禁线程池）；子进程崩溃不杀主进程（Rust 扩展崩溃可捕获，PRD 4.8.5）

### 实现 A6

- [ ] T041 [A6] 实现 `src/dataagent/platform/runtime/heartbeat.py` — 心跳按进展判定
- [ ] T042 [A6] 实现 `src/dataagent/platform/runtime/resources.py` — 资源预检 + 建议值 + 覆盖可行性校验 + 降批链
- [ ] T043 [A6] 实现 `src/dataagent/kernel/supervisor.py` — 控制面调度：状态机驱动 + 批次派发 + `resume_point` 续跑 + 批级事务边界；以三条角色消息（`ParseRequest` / `PageReport` / `OperatorResult`）为交互边界，禁止角色间长同步等待（contracts/role-messages.md）

**Checkpoint**: 续跑 / 心跳 / 资源 / 进程池测试全绿 → commit + push

---

## Phase 7: A7 交付骨架

**Goal**: CLI（up / status / logs / plugin / doctor / bench）+ `/status` 只读页 + Docker Compose + worker 进程壳。**验收 / 交付依赖。**

**Independent Test**: Docker 环境 `dataagent up` 自检→启动→打印地址、`/status` 页面渲染；`doctor` 对构造的端口占用 / 磁盘不足给出明确修复建议（US5 验收）；worker 进程壳无状态、禁 `asyncio.run`。

### 测试（TDD：先红后绿）⚠️

- [ ] T044 [A7] CLI 契约测试 `tests/e2e/cli/test_cli.py` — `dataagent up / status / logs -f / plugin list / doctor / bench` 存在且按 cli-api.md 行为；`doctor` 对端口占用、磁盘不足、模型缺失给出明确修复建议；CLI 唯一入口（Web 由 CLI 拉起）
- [ ] T045 [A7] `/status` 只读页测试 `tests/e2e/api/test_status.py` — GET `/status` 返回服务端渲染页面、**不改变任何状态**；GET `/health` 200；API 无状态（所有状态进 PG）
- [ ] T046 [A7] worker 进程壳测试 `tests/e2e/worker/test_worker.py` — worker 无状态（任务归属靠队列）、从队列取批、心跳按进展；`apps/worker` 内禁 `asyncio.run`（grep 门禁，G9）

### 实现 A7

- [ ] T047 [A7] 实现 `src/dataagent/apps/cli/` — `dataagent` 命令入口：up（自检→启动后端就绪→拉起 worker + API→打印 `http://127.0.0.1:8080`）/ status / logs -f / plugin list / doctor / bench 骨架（cli-api.md；CLI 是唯一入口，Web 由 CLI 拉起，同一进程两个壳）
- [ ] T048 [P] [A7] 实现 `src/dataagent/apps/api/` — 无状态 HTTP 骨架 + `/status` 只读页（SSR + 定时刷新，无构建链）+ `/health`；上传文件先进对象存储、不进 API 进程内存（骨架）
- [ ] T049 [A7] 实现 `src/dataagent/apps/worker/` — 进程壳：从队列取批 → 调 pipeline + `kernel/policies`（P0-A 期为空壳循环，真实阶段 P0-B 接入）→ 心跳 → 结果落库
- [ ] T050 [P] [A7] 实现 `deploy/` — Docker Compose（PG / Redis / MinIO + api + worker，同一份 compose 换外部后端地址即可迁移多机）+ Dockerfile + offline 包骨架（镜像 tar + wheel + 模型 + 字体目录约定，宪法「交付形态」）

**Checkpoint**: Docker 环境 up / status / doctor 全绿 → commit + push

---

## Phase 8: A8 决策点策略化

**Goal**: 8 个决策点 Policy 接口 + 页级 5 个规则实现 + 任务级 3 个 P0 规则阈值实现（M3 才加模型）。**路由 / 质检 / 修复 / 任务规划依赖。**

**Independent Test**: 页级 5 个 Policy 同一输入两次调用结果一致（确定性断言，宪法原则 I）；假实现可注入（测试不依赖模型）；输入 / 输出可序列化并写入 Trace。

### 测试（TDD：先红后绿）⚠️

- [ ] T051 [A8] 8 个 Policy Protocol 测试 `tests/unit/contracts/test_policies.py` — 逐字引用 contracts/policies.md 签名（PlanPolicy / ReplanPolicy / StrategyPolicy / RoutePolicy / TierPolicy / OperatorPolicy / AttemptPolicy / EscalationPolicy）；**无状态**（实例不得持有跨调用状态，入参出参可重放）；**可注入假实现**
- [ ] T052 [A8] 页级确定性测试 `tests/unit/kernel/policies/test_page_level.py` — Route / Tier / Operator / Attempt / Escalation 规则实现：同一输入两次调用结果一致；输入输出可序列化并写 Trace（页级决策参与审计）
- [ ] T053 [A8] 任务级规则阈值测试 `tests/unit/kernel/policies/test_task_level.py` — ReplanPolicy 阈值触发（如 40% 页失败 → 整本转 OCR）；StrategyPolicy 整本策略切换；**P0 用规则阈值，M3 才可加模型实现**（宪法原则 I + research D3）

### 实现 A8

- [ ] T054 [A8] 实现 `src/dataagent/contracts/policies.py` — 8 个决策点 Protocol（无内部依赖叶子模块）
- [ ] T055 [P] [A8] 实现 `src/dataagent/kernel/policies/rule_route.py` — RoutePolicy 规则实现（输入 PageProfile class/layout → 链路 ID）
- [ ] T056 [P] [A8] 实现 `src/dataagent/kernel/policies/rule_tier.py` — TierPolicy 规则实现（PageProfile + `extract_quality` → T0–T3）
- [ ] T057 [P] [A8] 实现 `src/dataagent/kernel/policies/rule_operator.py` — OperatorPolicy 规则实现（读 `pipeline/repair/mapping.py`，禁运行时推断）
- [ ] T058 [P] [A8] 实现 `src/dataagent/kernel/policies/rule_attempt.py` — AttemptPolicy 规则实现（三层停机：`changed == false` 立即停机、不重试同参数）
- [ ] T059 [P] [A8] 实现 `src/dataagent/kernel/policies/rule_escalation.py` — EscalationPolicy 规则实现（预算耗尽 → 升级人工）
- [ ] T060 [A8] 实现 `src/dataagent/kernel/policies/rule_replan.py` + `rule_strategy.py` — ReplanPolicy / StrategyPolicy P0 规则阈值实现（任务级）
- [ ] T061 [A8] 建立 `src/dataagent/kernel/policies/model_*.py` 占位文件（M3 才实现，**P0 不引用 pydantic-ai**）——满足 grep 门禁「`pydantic_ai` 仅 `kernel/policies/model_*`」，P0 期零引用、门禁恒绿

**Checkpoint**: 页级确定性 + 任务级阈值全绿 → commit + push

---

## Phase 9: A9 可观测与审计

**Goal**: 结构化日志 + span、指标采集（处理量 / STP 率 / 误放率 / 单页成本 / 档位分布 / 后处理命中率 / 时延 / 复杂页占比）、审计留痕、异常告警（FR-025）。**全部链路依赖。**

**Independent Test**: FR-025 指标清单逐项有采集点与口径；操作留痕可追溯；告警为旁路、不阻塞主链路。

### 测试（TDD：先红后绿）⚠️

- [ ] T062 [A9] 观测基础测试 `tests/unit/platform/observe/test_observe.py` — 结构化日志 + span 上下文；FR-025 指标清单（处理量 / STP 率 / 误放率 / 单页成本 / 档位分布 / 后处理命中率 / 时延 / 复杂页占比）逐项断言采集点与口径
- [ ] T063 [A9] 告警测试 `tests/unit/platform/observe/test_alerts.py` — 异常触发（STP 跌破阈值 / 换引擎占比异常上涨 / 队列积压 / 节点离线 / 失败分布漂移 / `plugin_reason_unknown` 占比 > 5%）；告警为旁路、不阻塞主链路

### 实现 A9

- [ ] T064 [P] [A9] 实现 `src/dataagent/platform/observe/log.py` — 结构化日志 + span 上下文
- [ ] T065 [P] [A9] 实现 `src/dataagent/platform/observe/metrics.py` — 指标采集（口径定义 + 落库 / 暴露）
- [ ] T066 [A9] 实现 `src/dataagent/platform/observe/audit.py` — 操作留痕（接入 / 配置 / 规则变更 / 人工修正全部可追溯，FR-025）
- [ ] T067 [A9] 实现告警规则与触发点 — 接入 supervisor 与报告生成处；`plugin_reason_unknown` 计数联动 B6（占比 > 5% 告警，US4 #3）

**Checkpoint**: 观测 / 告警测试全绿 → commit + push

---

## Phase 10: P0-A 集成门禁与收尾

**Purpose**: 证明基础支撑层整体成立，解锁 P0-B；工程门禁与工作流收尾。

**Independent Test**: P0-A 全部单测 + 集成全绿；`dataagent doctor / up` + `/status` + `plugin list` + 故障注入续跑演示全过（验收锚点 M1 中注册表 / CLI 唯一入口 / 续跑）。

- [ ] T068 跑 P0-A 集成验证（quickstart 子集）：空库 `docker compose up -d` → `dataagent doctor` → `dataagent up` → `/status` 渲染 → `plugin list` → kill -9 worker 续跑演示（对应验收锚点 M1：注册表、CLI 唯一入口、续跑）
- [ ] T069 回归必写测试盘点：**#1 页码基准（A3 已覆盖）、#5 主键内容哈希 / 幂等键（A2/A4 已覆盖）** 在 P0-A 全绿；#2 未知原因透传、#3 算子幂等、#4 无进展停机、#6 回路收敛的 P0-B 全量盘点见 `tasks-p0-b.md` T126（单向引用，避免两处维护）
- [ ] T070 [P] 初始化 `docs/CHANGELOG.md`（三类可复现性变更记录框架：插件版本 pin / 阈值 / 缺陷码增减，宪法「复现性承诺」）+ README 快速开始（uv sync + compose + doctor + up）
- [ ] T071 [P] ADR 记录：`docs/ADR/` 记录 P0-A 关键实现决策（sandbox 归 A5、`pages.py` 转换符号命名、A9 可观测模块、P0-A 单文件任务拆分口径）
- [ ] T072 全量回归 + 收尾：CI 四件套 + import-linter + 全部 grep 门禁一次跑绿；最终 commit + push

**Checkpoint**: 集成门禁全绿 → **解锁 P0-B**（B1–B9 任务已分解于 `tasks-p0-b.md`，可开始实现）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup（Phase 1 / A1）**: 无依赖，立即开始
- **A2（Phase 2）**: 依赖 A1 —— 阻塞一切
- **A3（Phase 3）**: 依赖 A2（defects/mapping 依赖 ids/errors）
- **A4（Phase 4）**: 依赖 A1（独立于 A2/A3，可在 A2 完成后并行推进）
- **A5（Phase 5）**: 依赖 A1 + A2（plugin 契约依赖 errors/ids）
- **A6（Phase 6）**: 依赖 A4 + A5（supervisor 用存储 + 注册表）
- **A7（Phase 7）**: 依赖 A1 + A6（worker 壳调 kernel；up 拉起后端）
- **A8（Phase 8）**: 依赖 A2 + A3（OperatorPolicy 读 mapping；任务级阈值）
- **A9（Phase 9）**: 依赖 A2 + A7（指标采集点、日志 / status 骨架）；可与 A8 并行
- **Phase 10（集成门禁）**: 依赖 A1–A9 全部完成

### 并行机会

- Phase 1 内 T002–T005 可并行；Phase 2 内 T008/T009（contracts 两个文件）可并行、T012–T015 可并行；Phase 3 内 T020 与 T017–T019 可并行；Phase 4 内 T026 与 T025 依序、T029 可并行；Phase 5 内 T034/T035 与 T036 可并行；Phase 6 内 T041/T042 与 T043 依序；Phase 7 内 T048/T050 与 T047 依序；Phase 8 内 T055–T059 五个页级规则实现全部并行
- A4 与 A2/A3 无依赖，可在 A2 完成后与 A3 并行推进（人手允许时）
- A9（可观测与审计）与 A8 无相互依赖，可与 A8 并行推进

### 每个 Phase 内

- 测试（TDD）MUST 先写、先 FAIL 再实现
- contracts（叶子）→ platform（支撑设施）→ kernel（内核）→ 实现核心
- 一个 Phase 完成且独立验收通过，再进入下一个

## Parallel Example: A2 核心机制

```bash
# 并行启动 A2 的两个契约文件（不同文件、无依赖）：
Task: "实现 src/dataagent/contracts/ids.py"
Task: "实现 src/dataagent/contracts/errors.py"
Task: "实现 src/dataagent/kernel/state.py"
Task: "实现 src/dataagent/kernel/budget.py"
```

## Implementation Strategy

### 增量交付（模块一个接一个）

1. 完成 Phase 1（A1 工程底座）→ 验证 CI 门禁全绿
2. 完成 A2 → 单测全绿 → commit + push
3. 完成 A3 → grep 门禁全绿 → commit + push
4. 完成 A4 → 双实现同一套件全绿 → commit + push
5. 完成 A5 → 注册表行为全绿 → commit + push
6. 完成 A6 → 故障注入续跑全绿 → commit + push
7. 完成 A7 → Docker 环境起停全绿 → commit + push
8. 完成 A8 → 页级确定性全绿 → commit + push
9. 完成 A9（可观测与审计）→ 观测 / 告警测试全绿 → commit + push
10. **STOP and VALIDATE**: 跑 Phase 10 集成门禁 → 通过后解锁 P0-B（任务已分解于 `tasks-p0-b.md`，按 B1→B9 顺序实现）

### 并行团队策略（多人时）

1. 团队先完成 A1
2. A2 与 A4 可并行（A4 仅依赖 A1）
3. A3 随 A2 之后；A5 随 A2 之后
4. A6 = A4 + A5 汇合；A7 = A6 之后；A8 可与 A6 并行（仅依赖 A2/A3）；A9 可与 A8 并行（依赖 A2 + A7）
5. 最后统一跑 Phase 10 集成门禁

---

## Notes

- [P] 任务 = 不同文件、无依赖
- [A1]–[A8] 标签将任务映射到对应模块，便于追溯（Setup 与收尾阶段无标签）
- 每个模块独立可测：完成即跑该模块 Independent Test
- **TDD 铁律**：测试先写、先红后绿，不得先实现后补测试（宪法 G8 NON-NEGOTIABLE）
- **每模块即推送**：每个 Phase 完成后 commit + push（依赖全局代理 `127.0.0.1:7890`）
- 避免：空泛任务、同文件冲突、跨模块依赖破坏独立性
- P0-B（B1–B9）不在此文件；全量拆解见 `tasks-p0-b.md`（与本文件同批完成、先于实现）；P1 暂不拆解
