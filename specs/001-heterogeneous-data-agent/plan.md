# Implementation Plan: 插件化异构数据处理 Agent（DataAgent）

**Branch**: `001-heterogeneous-data-agent` | **Date**: 2026-10-30 | **Spec**: specs/001-heterogeneous-data-agent/spec.md

**Input**: Feature specification from `/specs/001-heterogeneous-data-agent/spec.md`

## Summary

本计划覆盖 PRD V2.3 的 **P0（MVP）全部模块**：接入（文件夹 + 上传）、文件级体检、两级路由、PDF / CSV / TXT 插件化解析、页面画像、分级兜底、T1 后处理、四层质检、缺陷码与增强回路、RulePack、合并输出、分级交付、续跑、资源预检、存储抽象、插件注册表、CLI / 容器交付。

技术路线一句话：**一条确定性流水线：页级决策全部规则 + 任务级模型调用**（≤3 处、每任务 ≤8 次，PRD V2.3）。PDF 分类与抽取复用 pdf-inspector（`engine.pdf` 插件，双 capability）；**T1 后处理是 P0 最大自建项**；**基础支撑模块（配置 / 状态机 / 存储抽象 / 注册表 / 续跑 / CLI 骨架 / 测试基建）先行**，功能模块在它们之上开发。

## 执行优先级与里程碑

> 回答"先基础、后功能"的问题。依据 PRD 8.1 / 8.4 与宪法：基础设施"必须一开始就做对、后期改成本极高"，功能模块全部依赖它们。

### P0-A 基础支撑层（第一批 · 对应 M1）

**顺序执行，先于任何功能模块。** 这些模块不产生直接功能价值，但功能模块全部依赖：

| 序 | 模块 | 落点 | 依赖方 |
| -- | ---- | ---- | ---- |
| A1 | 工程底座：`pyproject.toml` + uv + 目录骨架 + CI 门禁（lint / mypy / pytest）+ TDD 基建 | 仓库根 | 一切 |
| A2 | 核心机制：配置单一 schema（pydantic-settings）、状态机唯一转移表、内容哈希 / uuidv7、错误码封闭集、三层预算 | `kernel/state.py` `kernel/budget.py` `contracts/ids.py` `contracts/errors.py` | 一切 |
| A3 | 全局共享机制：页码基准转换点 `contracts/pages.py`（全工程仅一处，1-indexed）、缺陷码→算子映射 `pipeline/repair/mapping.py`（写死） | `contracts/pages.py` `contracts/defects.py` `pipeline/repair/mapping.py` | 路由 / 质检 / 修复 |
| A4 | 存储抽象：对象存储双实现（本地盘 + S3）、队列最小语义（至少一次 + 手动确认 + 死信）、SQL 标准子集 + Alembic 迁移 | `platform/store/` `migrations/` | 接入 / 解析 / 续跑 |
| A5 | 插件注册表与加载器：能力索引 / 启停 / 版本锁定 / 依赖与兼容性校验 / 多实现显式优先级；热路径同进程、冷路径沙箱 | `kernel/registry.py` `platform/plugins/` `contracts/plugin.py` `platform/runtime/sandbox.py` | 路由 / 解析 / 质检 |
| A6 | 续跑与资源预检：落盘分层、批级事务边界、进程池隔离、心跳按进展判定、启动自检 + 建议值 + OOM 降批链 | `kernel/supervisor.py` `platform/runtime/` | 全部长任务 |
| A7 | 交付骨架：CLI（up / status / logs / plugin / doctor）+ `/status` 只读页 + Docker Compose + worker 进程壳 | `apps/cli/` `apps/api/` `apps/worker/` `deploy/` | 验收 / 交付 |
| A8 | 决策点策略化：8 个决策点 Policy 接口 + 页级规则实现 + 任务级配置开关（P0 规则阈值，M3 模型） | `contracts/policies.py` `kernel/policies/` | 路由 / 质检 / 修复 / 任务规划 |

**验收锚点（M1）**：7.1 #1–#8、#21、#30、#32（接入拦截、后缀无关判定、多 capability、批量抽取、画像两段写入、体检先于解析、注册表、CLI 唯一入口）。

### P0-B 功能模块层（第二批 · 对应 M2 / M3）

**在 A1–A7 之上按依赖顺序开发：**

| 序 | 模块 | 落点 | 依赖基础 |
| -- | ---- | ---- | ---- |
| B1 | 接入 + 文件级体检（11 项、三态、先于解析拦截） | `pipeline/ingest/` | A1 A4 |
| B2 | 一级格式判定器（自建：magic / 编码 / 分隔符，≤50ms / ≤256KB，三态） | `pipeline/sniff/` | A1 A2 |
| B3 | 两级路由 + `engine.pdf`（双 capability）+ `engine.csv` / `engine.text` 接入 | `pipeline/route/` `platform/plugins/adapters/` | A2 A5 B1 B2 |
| B4 | 页面画像两段写入（路由段 + 质量段，`route_reason` 原样保留） | `pipeline/profile/` | A2 B3 |
| B5 | L1 全量逐页质检 + L2 分层抽样 + 硬门控 + 性能档位 | `pipeline/quality/` | A2 B4 |
| B6 | T1 后处理（表格 cell 重建 / 页眉页脚剔除 / 跨页段重排）+ 缺陷码 + 增强回路 + 复杂页收口 + 降级路径 | `pipeline/repair/` | A3 B4 B5 |
| B7 | 合并（跨页表 / 标题层级 / 文档级校验）+ 分级交付 + 报告分层 + 输出 | `pipeline/merge/` `pipeline/output/` | A4 B5 B6 |
| B8 | RulePack + 受限表达式引擎 + `scope.page_filter` + 字段提取三层（P0 只做声明式层） | `platform/rules/` | A2 B3 |
| B9 | 黄金集（200 页）+ 标定脚本 + `extract_quality` 标定 + 评测 | `tests/` `pipeline/quality/` | B5 |
| B-PLAN | 任务级执行规划：Plan 解析 / 冻结 / 分支求值（FR-026，3 种 stage kind，禁自由图） | `kernel/plan.py` | A2 A8 B8 |

**验收锚点（M2 / M3）**：7.1 #9–#14、#17、#22–#28、#29；7.2 全部（黄金集标定、STP ≥ 80%、误放率 ≤ 2%、QC 自身达标）。

### P1 增强层（里程碑 M4 起）

DB / API 接入、`engine.ocr` / `engine.image` / `engine.pdfshift` 对照插件、分布式多节点（队列 + 背压 + 文档亲和）、NL→RulePack 编译、可观测告警、PII 掩码、队列抽象（NATS）、冷路径沙箱、`extractor` 类型插件、pipx 单机与离线安装包。验收：7.1 #18–#20、#31、#33；7.3。

### P2 远期（M5 起，按需）

全部图形界面、VLM-as-Judge、多租户、国产化适配、插件市场、MCP 出口、独立插件网关（满足 5.5 触发条件时立项）、关系库适配（达梦 / 人大金仓）。

## Technical Context

**Language/Version**: Python 3.12（pdf-inspector 仅发布 PyO3 绑定）

**Primary Dependencies**: pdf-inspector（`engine.pdf`，Rust 内核）、pydantic-settings（配置）、json-logic（受限表达式）、SQLAlchemy Core + Alembic（SQL 标准子集 + 迁移）、pytest + hypothesis（测试）、**uv（唯一包管理器，禁 pip / poetry / conda）**；禁止引入 Celery / Kafka / Ray / K8s / 向量库 / ORM / gRPC / LangChain。

**Storage**: PostgreSQL（状态 / 画像 / 报告 / 缺陷 / 动作，标准 SQL 子集）、Redis Streams（队列，仅最小语义）、MinIO + 本地盘（对象存储双实现，业务经 `platform/store/objectstore/`）。

**Testing**: pytest + hypothesis；testcontainers（PG / Redis / MinIO 集成）；自建 eval 脚本（黄金集标定 / 评测）；故障注入（kill -9、断网、磁盘满、毒丸、OOM）；性能 `dataagent bench`。TDD（测试先行，核心模块覆盖率 ≥ 90%）。

**Target Platform**: Linux 服务器（Docker Compose 主推交付）；Windows / macOS 仅开发环境。完全离线，数据不出域。

**Project Type**: CLI + HTTP API + 插件系统的库式应用（单一 src-layout 包 `src/dataagent/`）。

**Performance Goals**: 端到端 ≤ 150ms/页；吞吐 ≥ 30 万页/日/节点（16C64G）；PDF 页级抽取 p50 ≤ 16ms / p95 ≤ 40ms；分类 ≤ 30ms/文档；单进程峰值内存 ≤ 50 MB；调度响应 ≤ 1s。

**Constraints**: 确定性（模型只三处、输出封闭集 / 受约束 JSON）；内容哈希主键；单机多机同构（CLI 唯一入口、API 无状态、单一配置 schema、无"单机精简版"）；批级并行（默认 32 页、禁页级 fan-out、禁线程池、CPU 密集用进程池）；页码基准转换点全工程仅一处；uv.lock 随源码提交。

**Scale/Scope**: P0 = 7 个基础支撑模块 + 9 个功能模块 + 200 页黄金集；任务形态为"1000 份文件 / 数小时"长批处理。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | 宪法门禁 | 本计划的遵守方式 |
| -- | ------- | ---------------- |
| G1 | **确定性优先**：页级决策一律规则、可复现；任务级模型调用 ≤ 8 次/任务、Plan 冻结后执行确定；模型输出封闭集 / 受约束 JSON | 页级决策（路由 / 档位 / 算子 / 续试 / 升级）走 Policy 规则实现（A8），任务级走 Policy 模型实现（A8，M3），二者接口分离；`determinism` 字段驱动缓存与 T2 重跑合法性 |
| G2 | **复用优先**：`ocr_reasons_by_page` / `pages_needing_ocr` / `cmap_gaps` 原样落库；未知 reason 透传兜底桶；不 fork 插件源码 | B4 原样保留 `route_reason`；`reason → defect_code` 映射放配置、按插件版本分组、未知项归 `plugin_reason_unknown`（B6）；定制只做在外围后处理层 |
| G3 | **插件化与注册表**：新增格式 = 新增插件；注册表 P0 必备；禁后加载覆盖；禁插件间直接互调；不做独立网关 | A5 注册表含能力索引 / 版本锁定 / 依赖校验 / 显式优先级；热路径同进程、冷路径沙箱；无网关模块 |
| G4 | **二八成本与批级性能**：批级并行（默认 32 页）；L1 全量；成本阶梯 T0→T1→T3；进程池 | B3 / A6 批大小 32、文件内按批串行；B5 L1 全量 + 性能档位只削 L2；A6 进程池隔离；T1 为 P0 最大自建项（B6） |
| G5 | **职责分离**：QC 只读、经调度器、先落库再触发下游；缺陷必带 location；算子幂等且不改原产物 | 角色间仅三条结构化消息（contracts/role-messages.md）；B5 QC 只读；B6 缺陷码封闭集 + `location` 必填 + `changed == false` 停机 |
| G6 | **可续跑与内容寻址**：状态只在库；批级事务；先写内容再写索引；`resume_point`；主键 SHA-256 | A6 续跑三件套（PG + 对象存储 + 队列）+ 心跳按进展判定；data-model.md 主键铁律 |
| G7 | **包管理 uv（强制）** | A1 唯一允许 uv；CI grep 校验无 `pip install` / `poetry` / `conda`；`uv lock --check` |
| G8 | **TDD** | A1 建立测试门禁；六个必写测试（页码基准、未知原因透传、算子幂等、无进展停机、主键哈希、回路收敛）列入 B 系列验收 |
| G9 | **工程门禁**（机器可判） | CI grep：页码转换仅 `contracts/pages.py`、import-linter 分层单向、worker 禁 `asyncio.run`、禁直接 `redis.xadd` / `boto3.client`、算子禁 IO、新缺陷码同步 `pipeline/repair/mapping.py`、`pydantic_ai` 仅 `kernel/policies/model_*`、DDL 走 Alembic |
| G10 | **单机多机同构** | A7 CLI 唯一入口、API 无状态、配置单一 schema、对象存储 / 队列抽象保证换后端不改业务代码 |

**结论**：全部门禁通过，无违规，无需 Complexity Tracking 论证。

## Project Structure

### Documentation (this feature)

```text
specs/001-heterogeneous-data-agent/
├── plan.md              # This file
├── research.md          # Phase 0 决策与开放研究项
├── data-model.md        # 数据模型 + 状态机 + 主键铁律
├── quickstart.md        # 端到端验证指南
├── contracts/           # 外部接口契约
│   ├── taskspec.md      #   TaskSpec（任务规格，含 plan 段）
│   ├── plugin-manifest.md  #   插件 manifest 契约
│   ├── role-messages.md    #   角色间三条消息
│   ├── qc-output.md        #   质检器输出契约
│   ├── defect-codes.md     #   缺陷码封闭集
│   ├── cli-api.md          #   CLI 命令 + HTTP API + /status
│   └── policies.md         #   决策点策略化（8 个 Policy 协议）【新增】
└── tasks.md             # Phase 2 (/speckit-tasks) 生成
```

### Source Code (repository root)

```text
src/dataagent/
├── contracts/                 # 契约层（依赖=无）：ids / errors / pages★ / defects / plugin / policies
├── platform/                  # 支撑设施（依赖=contracts）：
│   ├── config.py              #   单一配置 schema（pydantic-settings）
│   ├── store/                 #   对象存储（local+s3）、队列（最小语义）、SQL 标准子集
│   ├── runtime/               #   heartbeat / resources / sandbox（执行设施）
│   ├── observe/               #   span、指标、日志、审计
│   ├── rules/                 #   RulePack + json-logic 表达式引擎
│   └── plugins/               #   loader + adapters/（engine_pdf / engine_csv / engine_text）
├── kernel/                    # 内核（依赖=contracts+platform）：
│   ├── state.py               #   状态机唯一合法转移表
│   ├── budget.py              #   三层预算
│   ├── plan.py                #   Plan 解析 / 冻结 / 分支求值【新增】
│   ├── registry.py            #   插件注册表
│   ├── supervisor.py          #   控制面调度（状态机驱动 + 批次派发）
│   └── policies/              #   Policy 实现：rule_*.py（P0）/ model_*.py（M3，唯一允许 pydantic-ai）
├── pipeline/                  # 处理阶段（依赖=contracts+platform+kernel）：
│   ├── ingest/  sniff/  route/  profile/  extract/
│   └── quality/  repair/  merge/  output/
└── apps/                      # 进程入口（依赖=全部）：
    ├── cli/                   #   up / status / logs / plugin / doctor / bench
    ├── api/                   #   HTTP 端点 + /status 只读页
    └── worker/                #   数据面批执行入口【新增】

migrations/                    # Alembic（所有 DDL 变更必须走迁移）
tests/                         # unit / integration / e2e / fault / bench
deploy/                        # Docker Compose / Dockerfile / offline
docs/                          # ADR 记录
```

**Structure Decision**: 单一 src-layout，收敛为 5 个一级包（`contracts / platform / kernel / pipeline / apps`），依赖方向 `apps → pipeline → kernel → platform → contracts` 单向、由 import-linter `layers` 契约强制执行（宪法「项目结构」节）。功能域（接入 / 路由 / 解析 / 质检 / 修复 / 输出）在 `pipeline/` 内各占一个目录；全局契约（页码转换、缺陷码、插件协议、Policy 接口）上提至 `contracts/`；控制逻辑收敛至 `kernel/`（含调度器 supervisor）；worker 为 `apps/worker/` 进程壳（调用 pipeline + kernel/policies，规避反向依赖）。

## Complexity Tracking

> 无需填写——Constitution Check 全部通过，无必须论证的违规项。

## Phase 1 设计产出（本命令生成）

| 产出 | 说明 |
| ---- | ---- |
| `research.md` | 关键技术决策（Decision / Rationale / Alternatives）与开放研究项 |
| `data-model.md` | 13 个实体 + 关系 + 状态机 + 主键铁律 + 校验规则 |
| `contracts/` | 7 份接口契约（TaskSpec / 插件 manifest / 角色消息 / QC 输出 / 缺陷码 / CLI+API / Policies） |
| `quickstart.md` | 端到端可运行验证场景（对应 spec 的 5 个用户故事 + FR-026 场景） |

**Constitution Check 复检**：Phase 1 设计未引入任何违反 G1–G10 的结构（角色消息限定三条、缺陷码封闭集、内容哈希主键、批级事务、uv 门禁、页码单一转换点），门禁保持通过。
