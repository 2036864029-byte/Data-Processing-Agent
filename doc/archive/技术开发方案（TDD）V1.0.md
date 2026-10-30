# 插件化异构数据处理 Agent 技术开发方案（TDD）V1.0

## 文档信息

| 项目 | 内容 |
|---|---|
| 文档名称 | 插件化异构数据处理 Agent 技术开发方案 |
| 版本号 | V1.0 |
| 编制日期 | 2026-10-30 |
| 上游依据 | `插件化异构数据处理 Agent 产品需求文档（PRD）V2.2`（唯一需求依据）+ `pdf-inspector.md`（插件接口细节） |
| 文档定位 | 回答"怎么做"：架构、技术选型、数据模型、接口、流程、并发、可靠性、测试、实施计划 |
| 不负责 | 不重复定义需求与验收阈值。凡与 PRD 冲突，**以 PRD 为准**，冲突处集中在第 17 章列出 |
| 读者 | 后端/数据工程、测试、运维、技术评审 |

## 阅读方式

- **要做技术评审**：读 1、2、3、17 章（约 40 分钟）
- **要开始写代码**：读 4、5、6、7 章，按 13 章的目录结构落文件
- **要部署交付**：读 8、9、10、11、12 章
- **要排期**：读 15、16 章

---

## 一、架构总览

### 1.1 一句话架构

> **一组无状态 worker 抢同一张任务表，每个 worker 是独立进程、自带插件、崩溃可被替换；所有状态与产物落在 PostgreSQL + 对象存储；控制面只负责决策与记账，数据面只负责算。**

这不是微服务架构，是**"共享数据库 + 多消费者"的批处理架构**。选它而不是微服务的理由写在 ADR-01 与 ADR-04。

### 1.2 进程与部署视图

```
                         ┌──────────────────────────┐
   浏览器 / CLI / 上游系统 ──▶│  api  (FastAPI, async)   │  无状态，可多副本
                         │  · HTTP API               │
                         │  · /status 只读页(SSR)    │
                         │  · 任务创建/查询/重试     │
                         └───────┬──────────────────┘
                                 │ 读写
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
     ┌────────────────┐  ┌──────────────┐  ┌────────────────┐
     │  PostgreSQL    │  │    Redis     │  │  对象存储       │
     │  · 状态机/画像  │  │ · 队列消息    │  │  · 页产物       │
     │  · 缺陷/动作    │  │ · 心跳 TTL   │  │  · 临时子集 PDF │
     │  · outbox      │  │ · 进度缓存    │  │  · 报告         │
     │  · span        │  │  （可丢）     │  │  · 渲染图(本地) │
     └───────┬────────┘  └──────┬───────┘  └───────┬────────┘
             │                  │                  │
             ▼                  ▼                  ▼
     ┌─────────────────────────────────────────────────────┐
     │  worker × N   (独立进程, 同进程内跑插件, 无状态)         │
     │  · 抢任务 → 批级解析 → L1 → 档位 → T1/入队增强 → 落盘    │
     │  · 崩溃由 supervisor / docker restart 拉起，从上一批续跑 │
     └─────────────────────────────────────────────────────┘
             ▲
             │ 只做巡检（超时重投 / 死信 / 清理 / 指标聚合）
     ┌───────┴────────┐
     │  janitor       │  单副本（多副本用 PG advisory lock 互斥）
     └────────────────┘
```

**四种进程，职责不重叠**：`api`（对外、无状态）、`worker`（计算、无状态）、`janitor`（巡检、幂等）、`cli`（运维入口，`up` 时拉起前三者）。

### 1.3 分层

| 层 | 模块 | 职责 | 有状态? |
|---|---|---|---|
| L1 接入层 | `ingest` | 接收、去重、登记、文件体检（4.1.8） | 否 |
| L2 判定层 | `sniff` | 一级格式判定器（4.2.5），出 `family` + `health` | 否 |
| L3 路由层 | `route` | 二级页级路由、链路注册表、决策 trace | 否 |
| L4 执行层 | `extract` `quality` `repair` | 批级解析、L1 特征、缺陷、增强回路 | 否 |
| L5 组装层 | `merge` `output` | 跨页拼接、文档级校验、三档交付、报告 | 否 |
| L6 支撑层 | `store` `runtime` `observe` `plugins` | 存储、进程、观测、插件注册表 | 是（在 PG） |

**层间只允许自上而下调用**，反向依赖通过事件（`TaskState` 变更）实现。这条不是洁癖：它保证了任何一层都能在测试里被单独拉起。

### 1.4 一次任务的数据流

```
文件 → [体检] health=ok|repairable|rejected
       ↓ ok/repairable
      [一级判定] family=pdf|tabular|text|image|unknown   ← 自建，≤50ms，≤256KB 采样
       ↓ pdf                                          ↓ tabular/text/image
      [二级路由] detect_pdf (1次/文档)                  [直接派 engine.csv/text/image]
       · pdf_type / pages_needing_ocr /              ↓
         pages_with_tables / is_complex              批级解析
       ↓                                              ↓
      [分批 32页] → [批级解析] markdown + text_items ──┘
       ↓
      [L1 特征向量 2KB] → [硬门控] pass/fail
       ↓ pass                    ↓ fail
      [序号合并]              [归因 → 判档 T0/T1/T2/T3]
       ↓                          ↓ T1            ↓ T2/T3
      [L2 抽样]              [后处理算子]      [重跑/换引擎 → 回到解析]
       ↓                          ↓
      [L3 文档级]  ←──────────────┘
       ↓
      [三档交付 + 报告 + gap 清单]
```

关键点：**L1 跟着批走，L2/L3 等全文档**。原因见 7.2。

### 1.5 六条可检验的设计原则

| # | 原则 | 怎么检验 |
|---|---|---|
| 1 | 判定与执行分离 | 质检模块的代码中不存在调用解析插件的路径（静态检查 import） |
| 2 | 路由是确定性代码 | 路由模块无 LLM 调用、无随机、无时间依赖；同输入同输出 |
| 3 | 进程边界即故障边界 | `kill -9` 任一 worker，任务在 30s 内被接续（6.3 验收） |
| 4 | 状态只在 PG，产物只在对象存储 | 重启全部进程后，任务可从 `resume_point` 继续 |
| 5 | 算子不改原产物 | 算子签名强制返回新对象，`OperatorResult.candidate` |
| 6 | 页码基准唯一转换点 | 全仓库搜 `+ 1` / `- 1` 出现在唯一文件中（`extract/wrapper.py`） |

---

## 二、技术选型

### 2.1 选型原则

1. **能少一个组件就少一个。** 私有化部署的失败大多来自环境，不是代码。每多一个中间件，`doctor` 就多一项、离线包就大一截、客户就多一个能装错的东西。
2. **不引入需要团队新学的技术。** 这个系统的复杂度已经在业务逻辑上，不应再叠加架构复杂度。
3. **二进制依赖必须可离线打包、可跨平台。** 政企环境无外网、可能 ARM。

### 2.2 语言与运行时

| 项 | 选择 | 理由 |
|---|---|---|
| 主语言 | **Python 3.11** | `pdf-inspector` 只发布 Python（PyO3）绑定，没有 Node/WASM 正式版。用 Python 是唯一不需要自建 FFI 的路径 |
| 版本下限 | 3.11 | 3.10 的 `ExceptionGroup`、`match` 语法成熟度不足；3.13 生态（尤其 PyO3 wheel）覆盖未验证 |
| 需要 Rust? | **不需要** | 不做 fork，不编译上游源码。见 ADR-10 |
| 类型检查 | mypy（`core/` `store/` `repair/` 严格模式） | 这三个模块的错误代价最高 |

### 2.3 选型表

| # | 领域 | 选择 | 主要备选 | 选它的理由 | 否决备选的理由 |
|---|---|---|---|---|---|
| 1 | Web 框架 | **FastAPI + uvicorn** | Flask, Django | 原生 async（API 层是纯 IO）、Pydantic 深度集成、OpenAPI 自动生成 | Flask 无 async 支持；Django 过重，ORM 会诱导用方言特性（违反 ADR-06） |
| 2 | Schema/校验 | **Pydantic v2** | attrs, dataclasses | 性能足够、JSON Schema 导出（插件契约校验直接复用）、`model_validate` 生态 | dataclasses 无运行时校验，插件边界必须校验 |
| 3 | 数据库 | **PostgreSQL 16** | MySQL 8, SQLite | JSONB 存 `location`/`evidence`、`FOR UPDATE SKIP LOCKED`、分区表、`LISTEN/NOTIFY` | MySQL 的 JSON 函数与 PG 不兼容且窗口函数弱；SQLite 不支持并发写（单机可作降级但非主选） |
| 4 | 驱动/查询 | **psycopg 3 + SQLAlchemy Core** | asyncpg, psycopg2, ORM | psycopg3 同时支持同步与异步，一套驱动两种引擎；SQLAlchemy **Core** 生成标准 SQL 子集，便于换库（ADR-06） | asyncpg 只有异步（worker 是同步的）；ORM 会隐藏方言差异 |
| 5 | 迁移 | **Alembic** | 手写 SQL | 与 SQLAlchemy Core 配套，可离线生成 SQL 供 DBA 审核 | 手写 DDL 在 P1 换达梦时无法自动化 |
| 6 | 队列 | **Redis Streams** | RabbitMQ, NATS, Kafka, PG 表 | PRD 已定为 Redis；消费组 + ACK + 死信够用；心跳复用同一组件（TTL） | Kafka 组件过重（JVM）；PG 表队列方案见 ADR-02 的取舍说明 |
| 7 | 对象存储 | **MinIO（S3 协议）+ 本地盘** | 直接读写 NFS | 双实现满足"单机也能跑"与"多机共享"；S3 协议可平滑迁到 OSS/COS | NFS 直挂会在多机并发写时出现一致性问题 |
| 8 | 客户端 | **boto3（同步）+ aioboto3（async API 层）** | minio-py | S3 协议通用，客户现场若已有 S3 兼容存储可直连 | minio-py 只支持 MinIO 特性，换云厂商要改代码 |
| 9 | PDF 操作（裁边/旋转/修复） | **pikepdf**（qpdf 绑定） | PyMuPDF | 纯 PDF 结构操作，**不重排内容流**；改 CropBox / Rotate 后坐标基准仍正确（D.3） | PyMuPDF 是 AGPL，商用需授权；且它会重写内容流，可能改变文本项 |
| 10 | PDF 渲染（image_based → 图） | **pypdfium2** | PyMuPDF, pdf2image | 自带二进制、Apache 许可、与 OCR 路径用的 PDFium 同一实现（版本可控） | pdf2image 依赖外部 poppler，离线打包更麻烦 |
| 11 | 编码检测 | **charset-normalizer** | chardet, cchardet | 纯 Python、对 GB18030/Big5 判断优于 chardet、无编译依赖 | chardet 已停止维护；cchardet 需编译 |
| 12 | CSV/文本解析 | **stdlib `csv` + 自写类型推断** | pandas, polars | 10 万行 ≤5s 的目标 stdlib 够用；无重依赖；类型推断规则要写进 RulePack，必须自己控制 | pandas 冷启动 ~0.5s、内存放大；polars 增加 30MB 包体且类型推断不可控 |
| 13 | 数值/聚类（表格 cell 重建） | **numpy** | scipy | 只需一维聚类与重叠判定，numpy 够；scipy 多 40MB | 引入 scipy 只为 `cluster` 不划算 |
| 14 | 图像处理 | **Pillow** | opencv-python | 只做尺寸校验、格式嗅探、缩略图；opencv 包体 60MB+ | 用完即弃的能力不值得背 60MB |
| 15 | 表达式引擎（RulePack / page_filter） | **json-logic（Python 实现）** | CEL, 自研 DSL | 声明式、可序列化、无代码执行风险；PRD 已列为首选 | 自研 DSL 要写解析器+校验器，成本远高于收益 |
| 16 | CLI | **Typer + Rich** | argparse, click | 类型安全、自动 help、Rich 提供进度条与表格 | argparse 手写子命令繁琐；click 无类型推导 |
| 17 | 配置 | **pydantic-settings + YAML** | dynaconf, 纯 dict | 校验与文档一体（`config schema` 可直接生成文档） | dynaconf 引入另一套优先级规则 |
| 18 | 日志 | **structlog（JSON）** | logging + 自写 formatter | 结构化字段天然可检索，`doc_id`/`batch_id` 作为 context 贯穿 | 标准 logging 的 JSON formatter 需要自己维护 |
| 19 | 指标 | **prometheus_client** | OpenTelemetry Metrics | 拉模型、零依赖、`/metrics` 一行暴露 | OTel 需要 collector，私有化多一个组件 |
| 20 | 追踪 | **自建 span 表（PG）+ 可选 OTel 导出** | Jaeger, Tempo | PRD 5.5 要求的 span 字段很少（插件 id/版本/耗时/字节/cache_hit），PG 表够用且有 SQL 查询能力 | Jaeger 是另一个服务，私有化不值得 |
| 21 | 测试 | **pytest + pytest-asyncio + pytest-xdist + hypothesis** | unittest | hypothesis 用于编码检测与分隔符判定的属性测试（这类逻辑 bug 最多） | — |
| 22 | 打包 | **hatchling wheel + uv（开发）+ Docker Compose（交付）** | poetry, pip-tools | uv 安装快、锁文件可复现；wheel 是 pipx 单机形态的前提 | poetry 解析慢，且与 uv 生态割裂 |
| 23 | 代码质量 | **ruff + mypy + pre-commit** | flake8+isort+black | 一个工具覆盖 lint/format/import 排序 | 三件套配置成本高且规则重叠 |

### 2.4 明确不引入的东西

这一节比上一节重要。以下每一项都有人会提，逐条说明为什么不做：

| 不做 | 常见理由 | 为什么不做 |
|---|---|---|
| **Celery / RQ / Dramatiq** | "任务队列标准方案" | 它们的价值是"任务分发 + 重试 + 定时"，而我们的重试语义是"换操作不是重跑"（4.5.8），与 Celery 的 `retry` 语义直接冲突。用它反而要绕开它的重试机制 |
| **Kafka** | "高吞吐" | 3.5 页/秒的场景用 Kafka 是浪费。而且它要求 JVM，离线包体积翻倍 |
| **Ray / Dask** | "分布式计算" | 我们需要的是"文档级分片 + 亲和性"，不是算子级并行。Ray 解决的是另一个问题 |
| **Kubernetes** | "云原生" | 私有化客户多数只有几台机器，`docker compose` 足够。K8s 会把部署失败率抬高一个量级。P2 若有客户要求再提供 Helm chart |
| **向量库** | "RAG 都需要" | 本产品输出 markdown + JSON，不做检索。检索是下游的事 |
| **ORM（SQLAlchemy ORM 层）** | "开发快" | 会隐藏方言差异，P1 换达梦时踩雷（ADR-06）。用 Core 表达查询，多写 20% 代码，换可移植性 |
| **gRPC 插件网关** | "插件解耦" | 同进程高频调用加 RPC 层，成本翻 2–20 倍（PRD 5.5）。ADR-04 |
| **LLM 框架（LangChain 等）** | "Agent 必备" | 全文只有 3 个模型调用点，且都是"结构化输出 + 校验"。自己写 200 行比引入框架更可控 |

### 2.5 依赖清单与体积预算

| 类别 | 包 | 体积（约） |
|---|---|---|
| 核心 | fastapi, uvicorn, pydantic, sqlalchemy, psycopg[binary], alembic | 40 MB |
| 存储 | boto3, redis | 25 MB |
| PDF | pdf-inspector, pikepdf, pypdfium2, Pillow | 90 MB（pypdfium2 含 PDFium ~30MB） |
| 文本 | charset-normalizer, json-logic | 5 MB |
| CLI/日志 | typer, rich, structlog, prometheus-client | 15 MB |
| 开发 | pytest, ruff, mypy, hypothesis | 不进镜像 |
| **镜像合计（含 Python 运行时）** | | **≈ 450 MB** |
| OCR 可选层 | onnxruntime + PP-OCR 模型 | +350 MB（仅 P1，独立镜像 tag） |

> 体积是交付约束：离线包按这个数预留 U 盘/内网传输空间。OCR 层必须可分离，否则所有客户都要为用不到的能力付 350MB。

---

## 三、关键技术决策（ADR）

每条决策给出：背景 → 决策 → 理由 → 代价 → 落地约束。**代价必须写出来，没有代价的决策通常是没想清楚。**

### ADR-01 多进程独立消费，不做进程池派发

**背景**：PRD 4.8.5 要求"必须用进程池，不能用线程池"，因为 Rust 扩展段错误会杀掉整个进程。

**决策**：不用 `ProcessPoolExecutor` 做父子派发。改为 **N 个独立 worker 进程，各自从队列领取任务**，由 `supervisor`（CLI 父进程）或容器 `restart: always` 负责拉起。

```
错误做法（进程池派发）              采用做法（独立消费者）
主进程 ──派活──▶ 子进程1             worker1 ─┐
       ──派活──▶ 子进程2             worker2 ─┼─▶ 抢队列
       ◀─收结果──                   worker3 ─┘
子进程死 → 主进程要重派、要记账        子进程死 → 队列消息未被 ACK，超时后自动重投
```

**理由**：
1. 崩溃隔离天然成立——没有"父进程派发状态"需要维护，死掉的 worker 手上的消息因未 ACK 而被重新投递。
2. 单机 → 多机零改动：多机只是"更多进程连同一个队列"（PRD 4.8.8 五条约束之一）。
3. 心跳与续跑逻辑只需一套（在 worker 外部），不需要在父子间同步。

**代价**：每次任务领取多一次队列往返（< 1ms，PRD 6.1 允许 1s 的调度响应）。相比派发模型，失去了"父进程统一控制并发度"的能力——改用队列预取数（prefetch）与 worker 数配置来控制。

**落地约束**：worker 进程启动后必须**先注册心跳再领任务**，否则 janitor 会把刚启动的 worker 判为死亡。

### ADR-02 Redis Streams 作队列，用事务性 Outbox 消除双写

**背景**：任务状态在 PG，消息在 Redis。若"改状态 + 投消息"分两步做，中间崩溃会导致状态变了但没人处理，或消息投了但状态没变。

**决策**：Redis Streams（消费组 + ACK + 死信）+ **Transactional Outbox**：

```sql
BEGIN;
  UPDATE task SET state='ROUTED' WHERE task_id=$1 AND state='CLASSIFIED';
  INSERT INTO outbox(msg_id, topic, payload, created_at) VALUES (...);
COMMIT;   -- 状态与"待投递"同一事务，原子
```

独立的投递器（janitor 的一个子任务）扫描 `outbox WHERE published_at IS NULL`，投递到 Redis 后回写 `published_at`。消费方按 `msg_id` 幂等（`INSERT ... ON CONFLICT DO NOTHING` 到 `consumed_msg`）。

**理由**：这是唯一能在"状态在关系库、消息在中间件"的组合下保证**至少一次 + 不丢 + 不重复处理**的标准做法。没有 outbox，"重新入队"只能靠扫描状态表，而扫描无法区分"已投递待消费"与"根本没投递"。

**代价**：多一张表、多一个投递循环、消息延迟增加 1 个扫描周期（默认 200ms）。可接受——PRD 要求调度响应 ≤1s。

**落地约束**：
- outbox 表必须有 `published_at` 索引与 TTL 清理（保留 7 天）
- 投递器必须多副本安全（PG advisory lock）
- **禁止**在业务代码里直接 `redis.xadd`，必须经 `store/queue.publish()`（静态检查）

> **与 PRD 的差异**：PRD 4.8.5 把队列消息归为"可丢"，本方案实际做到了不丢。这不是偏离，是加强——因为"可丢"的前提是"能从 PG 重建"，而重建需要扫描，扫描需要区分已投/未投，outbox 正是那个区分依据。

### ADR-03 API 异步、worker 同步，两种并发模型并存

**背景**：PRD 4.3.8 明确"吞吐来自进程数，不来自事件循环"。

**决策**：

| 进程 | 并发模型 | 依据 |
|---|---|---|
| `api` | asyncio（uvicorn worker） | 1000 份文件上传、200 并发进度查询，全是 IO 等待 |
| `worker` | 同步阻塞 + 多进程 | CPU 密集；且崩溃隔离要求进程级隔离 |
| 外部增强器调用（在 worker 内） | 有界线程池（默认 4） | 单次等待数十秒的 IO，进程内并发即可，不需要 asyncio 侵入整条同步链路 |

**理由**：把一个同步的、CPU 密集的批处理循环改成 async，收益是零、复杂度是全额。反过来，把 200 并发的 API 写成同步，每个请求占一个线程，1000 并发时就崩。

**代价**：两套代码风格并存，团队需要理解"哪里用 async"。

**落地约束**：
- `worker` 内**禁止** `asyncio.run`（静态检查）
- `api` 内**禁止**同步阻塞调用超过 10ms 的函数（`boto3` 需用 `aioboto3`；CPU 计算不许在 handler 里做）
- 两套代码共用 `store/` 与 `core/`，但 `store` 必须同时提供 sync/async 两套入口（psycopg3 支持）

### ADR-04 插件注册表必须有，网关不做

**背景**：PRD 5.5 已定。此处只落实现形态。

**决策**：
- 注册表 = **PG 表 + 启动时扫描 manifest**，只做元数据查询，不参与调用。
- 热路径插件（`engine.pdf`、`validator.l1`、T1 算子）= **同进程 Python 对象**，直接函数调用。
- 冷路径插件（OCR、外部增强器、connector、exporter、extractor）= **子进程 + JSON 文件交换 + 资源限制**，由 `runtime/sandbox.py` 拉起。

**理由**：注册表的收益（能力索引、启停、版本锁定、灰度）与调用形态无关；网关的成本（每次序列化 + RPC）与调用频次成正比。这两件事被混为一谈是最常见的架构误判。

**代价**：热路径插件无法独立升级（要随主程序发版）；冷路径插件无法享受同进程的性能。

**落地约束**：
- `trust: unsigned` 的插件**强制** `sandbox != none`，加载时校验（PRD 5.1 验收 4）
- 同名能力多实现按显式 `priority` 排序，**禁止**按加载顺序覆盖
- 插件加载失败必须**降级为不可用并告警**，不能使整个系统启动失败

### ADR-05 文档与页产物用内容哈希作主键

**背景**：PRD 5.2 铁律。

**决策**：
```python
doc_id       = sha256(file_bytes)                      # 32 字节 hex
artifact_id  = sha256(doc_id | page_range | engine | params_hash | rulepack_hash)
```
对象存储 key 直接由 ID 推导：`artifacts/{doc_id[:2]}/{doc_id}/{artifact_id}.json.zst`

**理由**：换存储后端时 key 不变，搬迁即复制；天然去重（同文件重复上传零成本）；天然幂等（同参数重算 key 相同，可直接命中缓存）。

**代价**：`doc_id` 无法携带业务信息；文件名重复但内容不同会得到不同 ID（这是正确行为）；哈希计算需读全文件（1000 份 × 平均 5MB = 5GB IO，实测 < 10s，可接受）。

**落地约束**：哈希按流式计算（8MB chunk），禁止 `read()` 全文件进内存。

### ADR-06 标准 SQL 子集 + SQLAlchemy Core，不用 ORM

**背景**：P2 可能要换达梦/人大金仓。

**决策**：约束清单（写进 CI 检查）：

| 禁止 | 替代 |
|---|---|
| 存储过程、触发器 | 应用层逻辑 |
| JSONB 运算符查询（`->>`, `@>`） | JSON 字段整体读写，过滤在应用层 |
| 数据库端 `date`/`timestamptz` 语义依赖 | 统一存 UTC 毫秒整数（`bigint`） |
| 自增序列做业务主键 | 内容哈希 / UUIDv7 |
| `RETURNING`（PG 特有）用于业务逻辑 | 写后读，或 `INSERT ... ON CONFLICT` 标准语法 |
| 窗口函数的高级用法 | 子查询 + 应用层聚合 |

**理由**：ORM 会"假装兼容"然后在隔离级别、锁行为、JSON 操作上给惊喜。批处理系统最怕"看起来一样、行为不一样"。

**代价**：JSON 过滤在应用层做，`Defect` 按 `location.table_index` 查询需要拉取后过滤（数据量可控：单页缺陷 < 20 条）。

**落地约束**：`location` / `evidence` 用 `jsonb` 存（PG）或 `text` 存（MySQL/达梦），**读取时永远整取**，代码里不许出现 `->>`。

### ADR-07 页码基准唯一转换点

**背景**：`pdf-inspector` 的 `classify_pdf` 是 0-indexed；`extract_pages_markdown` 入参与 `pages[].page` 是 0-indexed，但同对象的 `pages_with_tables` 是 1-indexed；`detect_pdf` / `TextItem.page` 是 1-indexed。

**决策**：系统内部**一律 1-indexed**。转换只允许发生在 `extract/wrapper.py` 一个文件里：

```python
# extract/wrapper.py —— 全仓库唯一允许出现页码偏移的文件
def to_plugin_pages(page_nos: Sequence[int]) -> list[int]:
    """1-indexed → 插件入参（0-indexed）"""
    return [p - 1 for p in page_nos]

def from_plugin_page(p: int) -> int:
    """插件 PageMarkdown.page（0-indexed）→ 1-indexed"""
    return p + 1
```

**理由**：200 页文档里"差一页"表现为"第 82 页的问题记为第 83 页"，人工核对几乎发现不了，但会把缺陷定位、修复回填、报告页码全部带偏。

**代价**：wrapper 层多一层薄封装，调用链多一跳（纳秒级）。

**落地约束**：
- 单测断言：`extract_pages_markdown(path, pages=[k])` → `pages[0].page == k` 且（若该页有表）`k+1 in pages_with_tables`
- CI 检查：`grep -rn "+ 1\|- 1" --include=*.py src/ | grep -v extract/wrapper.py` 出现页码语义的偏移即失败

### ADR-08 增强算子是同进程纯函数，不做插件

**背景**：PRD 4.5.8 要求。

**决策**：
```python
@dataclass(frozen=True)
class OperatorResult:
    candidate: PageArtifact | None   # None = 放弃
    changed: bool                    # 停机机制的核心依据
    cost: int                        # T1 固定 0
    notes: str

def operator(artifact: PageArtifact, defect: Defect, ctx: OpContext) -> OperatorResult: ...
```

**理由**：算子是每页可能被调 2–3 次的高频操作，本身只需毫秒级。跨进程序列化一份 200–400KB 的 artifact，开销比计算本身大 20–100 倍。

**代价**：算子崩溃会带走 worker 进程（由 ADR-01 的进程隔离兜底，损失一批）。

**落地约束**：
- 算子内部**禁止** IO（无文件、无网络、无数据库）
- 算子**必须幂等**且不修改入参（`PageArtifact` 用 frozen dataclass）
- `changed=False` 时调度器立即标记该码"此路不通"，不再尝试同码

### ADR-09 质检判定只消费特征向量（≈2KB）

**背景**：PRD 4.3.7 的跨进程边界结论。

**决策**：
```
解析进程内：artifact ──▶ extract_features() ──▶ FeatureVector(≈2KB) ──▶ 门控判定
                                │
                                └──▶ 缺陷（带 location）──▶ 算子（必须同进程）
```

**理由**：L1 的输入是 10 来个浮点数与一张缺陷清单，不是完整坐标结构。把边界划在特征向量上，判定逻辑将来可以外置（进独立进程或独立服务），而修复永远同进程。

**代价**：特征提取本身必须在解析进程内完成（否则要先传完整 artifact，得不偿失）；特征集变更需要同步改两端。

**落地约束**：`FeatureVector` 必须是 flat 的 `dict[str, float|int]`，便于落库与标定（PRD 4.4.7）；新增特征必须同时更新标定集。

### ADR-10 不 fork pdf-inspector 源码

**背景**：PRD 风险 R9。

**决策**：只用 pip 安装的 wheel，任何定制都做在外围（后处理、换输入、换算子）。

**理由**：成本优势全部来自"不改上游"。一旦改 Rust 源码：失去上游升级能力、失去官方 benchmark 的性能数字、需要自己维护交叉编译（含 Windows/ARM）、R10 的国产化验证要重做一遍。

**代价**：某些表格/分栏缺陷无法从源头解决，只能靠 T1 后处理绕过（这也是 T1 必须做厚的原因）。

**落地约束**：
- `pip freeze` 中的 `pdf-inspector` 版本号必须与 `manifest.api_version` 对应关系记录在 `deploy/pinned.txt`
- 升级流程：跑黄金集 → 对比 STP/TEDS/标题层级 → 通过才允许改 pin（15.4）
- 若真遇到无法绕过的缺陷，先提 issue；确认不修再评估 fork，且 fork 必须**单独分支 + 每季度 rebase**（写入风险登记册）

### ADR-11 状态页服务端渲染，不引入前端构建链

**背景**：PRD 要求 P0/P1 不交付界面，但"看得见进度"不能拖到 P2。

**决策**：一个路由 `/status`，Jinja2 模板 + `<meta http-equiv="refresh" content="3">`，约 250 行。可选 htmx（一个 14KB 的 JS 文件）做局部刷新。

**代价**：刷新会闪；交互能力仅限"看"。

**落地约束**：该页**必须**只调用 P2 正式前端将使用的同一批 API（`GET /tasks`, `GET /tasks/{id}`, `GET /metrics/summary`），不许为它开后门接口。

### ADR-12 单一配置 schema，单机与多机同构

**背景**：PRD 4.8.8。

**决策**：一个 `config.yaml` + 环境变量覆盖，`queue` / `storage` / `db` 三个 backend 字段决定连接目标。单机 = 指向本机容器；多机 = 指向外部服务。**代码中不存在"单机模式"分支。**

**代价**：单机部署也要起 PG/MinIO 容器（约 500MB 内存开销）。对小客户是浪费，但换来"不会出现没人测过的第二套代码路径"。

**落地约束**：CI 中用两套配置各跑一次集成测试（全本地 backend / 全外部 backend），任一失败即阻塞合并。

---

## 四、系统架构详细设计

### 4.1 进程职责

| 进程 | 副本数 | 职责 | 不允许做 |
|---|---|---|---|
| `api` | 1–N | HTTP 接口、任务创建、状态查询、`/status` SSR、文件上传（流式写对象存储） | 不允许执行解析、不允许持有任务状态在内存 |
| `worker` | N（≥2） | 抢任务、批级解析、L1 特征、档位判定、T1 算子、入队增强、落盘 | 不允许直接改文档级汇总（由 `merge` 阶段统一做） |
| `janitor` | 1（advisory lock） | outbox 投递、超时任务重投、死信处理、span/outbox 清理、指标聚合 | 不允许执行业务计算 |
| `cli` | 按需 | `up/down/status/logs/plugin/doctor` | 不允许绕过 API 直接改状态（除 `doctor` 的只读检查） |

### 4.2 worker 主循环（伪代码）

```python
def main():
    heartbeat.register()                      # 先注册再领任务（ADR-01）
    reg = PluginRegistry.load()               # 加载/校验插件，失败降级
    limits = ResourceProbe.suggest(reg)        # 4.8.6 的建议值，可被配置覆盖

    while not stopping:
        if not ResourceGate.admit(limits):     # 内存<20% / 磁盘不足 / CPU 过载
            sleep(1.0); continue

        msg = queue.claim(timeout=5)           # 未 ACK 的消息超时自动重投
        if msg is None:
            continue

        try:
            with heartbeat.progress(msg) as hb:        # 按"页推进"报心跳，不按耗时
                run_batch(msg, reg, limits, hb)
            queue.ack(msg)
        except CrashedBatch as e:                    # 单批异常：不影响其他批
            record_batch_failure(msg, e)             # 写 PageGap
            queue.ack(msg)                            # 不重投，避免毒丸循环
        except FatalError:
            queue.nack(msg)                           # 交回队列，换 worker 继续
            raise                                     # 让进程退出，由 supervisor 拉起
```

**三个要点**：
1. `claim` 前检查资源，`claim` 后按批估算再降批（4.8.6 的 A + 估算层）。
2. 心跳由"处理到第几页"驱动，慢批不会被误杀（PRD 4.8.5）。
3. `CrashedBatch` 与 `FatalError` 必须分开：前者是"这个文件有毒"，后者是"这个进程不可信"。

### 4.3 模块清单

| 模块 | 关键类/函数 | 输入 → 输出 | 依赖 |
|---|---|---|---|
| `sniff.pipeline` | `detect_family(bytes) -> FamilyVerdict` | bytes → family + confidence + evidence | `magic`, `encoding`, `delimiters` |
| `route.decision` | `decide(doc, classification, registry) -> RoutePlan` | 分类结果 → `[(chain, pages, engine, params)]` | registry |
| `extract.wrapper` | `PdfEngineAdapter` | bytes + pages → `PageArtifact[]` | pdf-inspector |
| `extract.batching` | `plan_batches(pages, size, hints) -> Batch[]` | 页列表 → 批列表（OCR 页单独成批） | — |
| `quality.features` | `extract_features(artifact) -> FeatureVector` | artifact → 2KB 特征 | numpy |
| `quality.gates` | `judge(fv, profile, tier) -> PageReport` | 特征 → pass/fail + 缺陷 | 阈值配置 |
| `repair.mapping` | `MAP: dict[DefectCode, OperatorSpec]` | 码 → 算子（写死） | — |
| `repair.loop` | `enhance(artifact, defects, budget) -> Candidate` | 缺陷 → 候选 | operators |
| `merge.assembly` | `assemble(doc, artifacts) -> DocArtifact` | 页产物 → 文档产物（gap 保留占位） | — |
| `output.report` | `build_report(doc_reports) -> TaskReport` | 文档报告 → 分层报告 | — |
| `plugins.registry` | `PluginRegistry` | manifest → 注册表 + 能力索引 | PG |
| `runtime.resources` | `ResourceProbe` | 机器指标 → 建议参数 | psutil |

### 4.4 与 PRD 章节的对应

| PRD 章节 | 实现模块 |
|---|---|
| 4.1.8 文件体检 | `ingest/health.py` |
| 4.2.5 一级判定 | `sniff/*` |
| 4.3.3 PDF 插件接入 | `plugins/adapters/engine_pdf.py` + `extract/wrapper.py` |
| 4.3.5 页面画像 | `profile/writer.py` |
| 4.3.7 内存预算 | `extract/batching.py` + `runtime/resources.py` |
| 4.4 L1/L2/L3 | `quality/*` |
| 4.5 缺陷与增强 | `repair/*` |
| 4.6 规则 | `rules/*` |
| 4.7 合并输出 | `merge/*` + `output/*` |
| 4.8 续跑与资源 | `runtime/*` + `store/*` |
| 5.5 插件注册表 | `plugins/*` |

---

## 五、数据模型与存储设计

### 5.1 实体关系

```
task 1─N document 1─N page_profile
                   1─N page_artifact ──N page_report ──N defect
                   1─N page_gap
                   1─N doc_report
       1─N action
plugin_registration（全局）
span / outbox / consumed_msg（基础设施）
```

### 5.2 表设计

```sql
-- 时间统一 UTC 毫秒整数（ADR-06）；主键用内容哈希或 UUIDv7
CREATE TABLE task (
  task_id        TEXT PRIMARY KEY,              -- uuidv7
  spec           JSONB NOT NULL,                -- TaskSpec（PRD 4.1.4）
  state          TEXT NOT NULL,                 -- 状态机枚举，见 5.4
  budget         JSONB NOT NULL,                -- 剩余预算（次数/成本/墙钟）
  resume_point   JSONB,                         -- {doc_id, batch_no} 最后完成的批
  stats          JSONB NOT NULL DEFAULT '{}',   -- 页数/文档数/T3 占比等累加
  owner          TEXT,                          -- 租户/用户（P2 多租户预留）
  created_at     BIGINT NOT NULL,
  updated_at     BIGINT NOT NULL
);
CREATE INDEX ix_task_state_updated ON task(state, updated_at);

CREATE TABLE document (
  doc_id         TEXT PRIMARY KEY,              -- sha256(文件)，ADR-05
  task_id        TEXT NOT NULL REFERENCES task(task_id),
  filename       TEXT NOT NULL,
  size_bytes     BIGINT NOT NULL,
  family         TEXT NOT NULL,                 -- pdf|tabular|text|image|unknown
  health         TEXT NOT NULL,                 -- ok|repairable|rejected
  health_detail  JSONB,                         -- 11 项体检结果（PRD 4.1.8）
  page_count     INT,
  state          TEXT NOT NULL,
  created_at     BIGINT NOT NULL
);
CREATE INDEX ix_doc_task ON document(task_id, state);

CREATE TABLE page_profile (
  doc_id         TEXT NOT NULL REFERENCES document(doc_id),
  page_no        INT NOT NULL,                  -- 1-indexed（ADR-07）
  class_json     JSONB,                         -- 路由段：pdf_type/class_confidence/route/route_reason[]
  layout_json    JSONB,                         -- 路由段：columns/has_table/is_complex/rotation
  signals_json   JSONB,                         -- 解析后：char_density/glyph_coverage/...
  quality_json   JSONB,                         -- 解析后：extract_quality/tier
  updated_at     BIGINT NOT NULL,
  PRIMARY KEY (doc_id, page_no)
);
-- 分两次写入：路由时写前两列，解析后补后两列（PRD 4.3.5 时序）

CREATE TABLE page_artifact (
  artifact_id    TEXT PRIMARY KEY,              -- 内容寻址，ADR-05
  doc_id         TEXT NOT NULL,
  page_range     INT4RANGE NOT NULL,            -- 批级产物：[start, end)
  engine         TEXT NOT NULL,                 -- engine.pdf@1.0.0
  params_hash    TEXT NOT NULL,
  tier           TEXT NOT NULL,                 -- T0|T1|T2|T3
  object_key     TEXT NOT NULL,                 -- 指向对象存储，内容不落库
  size_bytes     BIGINT NOT NULL,
  created_at     BIGINT NOT NULL,
  UNIQUE (doc_id, page_range, engine, params_hash)
);
-- 注意：content（markdown + text_items）不存 PG，只在对象存储（PRD 4.8.5）

CREATE TABLE page_report (
  doc_id         TEXT NOT NULL,
  page_no        INT NOT NULL,
  artifact_id    TEXT NOT NULL REFERENCES page_artifact(artifact_id),
  verdict        TEXT NOT NULL,                 -- pass|fail|uncertain
  features       JSONB NOT NULL,                -- FeatureVector（ADR-09，≈2KB）
  fidelity       REAL,                          -- 需真值时才有（L2）
  qc_confidence  REAL,                          -- QC 对自身判定的置信度
  created_at     BIGINT NOT NULL,
  PRIMARY KEY (doc_id, page_no, artifact_id)
);

CREATE TABLE defect (
  defect_id      TEXT PRIMARY KEY,
  doc_id         TEXT NOT NULL,
  page_no        INT NOT NULL,
  code           TEXT NOT NULL,                 -- 11 个封闭码之一（PRD 4.5.7）
  location       JSONB NOT NULL,                -- 必须可操作，无 location 不入库
  evidence       JSONB NOT NULL,
  severity       REAL NOT NULL,
  raw_reason     TEXT,                          -- code=plugin_reason_unknown 时必填
  created_at     BIGINT NOT NULL
);
CREATE INDEX ix_defect_doc_page ON defect(doc_id, page_no);
CREATE INDEX ix_defect_code ON defect(code);

CREATE TABLE page_gap (
  doc_id         TEXT NOT NULL,
  page_no        INT NOT NULL,
  defect_codes   TEXT[] NOT NULL,
  tried_ops      TEXT[] NOT NULL,
  recommended    TEXT NOT NULL,                 -- local_ocr|mineru|mark_only|none
  fallback_used  TEXT,
  created_at     BIGINT NOT NULL,
  PRIMARY KEY (doc_id, page_no)
);

CREATE TABLE action (
  action_id      TEXT PRIMARY KEY,
  doc_id         TEXT NOT NULL,
  page_no        INT NOT NULL,
  code           TEXT NOT NULL,                 -- 触发它的缺陷码
  op             TEXT NOT NULL,
  tier           TEXT NOT NULL,
  changed        BOOLEAN NOT NULL,              -- 停机依据（PRD 4.5.9）
  cost           INT NOT NULL,
  notes          TEXT,
  created_at     BIGINT NOT NULL
);

CREATE TABLE plugin_registration (
  plugin_id      TEXT NOT NULL,
  version        TEXT NOT NULL,
  kind           TEXT NOT NULL,                 -- engine|validator|extractor|connector|exporter
  api_version    INT NOT NULL,
  manifest       JSONB NOT NULL,
  determinism    TEXT NOT NULL,
  trust          TEXT NOT NULL,
  sandbox        TEXT NOT NULL,
  priority       INT NOT NULL DEFAULT 100,      -- 多实现选择，禁止按加载顺序
  enabled        BOOLEAN NOT NULL DEFAULT TRUE,
  installed_at   BIGINT NOT NULL,
  PRIMARY KEY (plugin_id, version)
);

CREATE TABLE span (                             -- 插件调用观测（PRD 5.5）
  span_id        TEXT PRIMARY KEY,
  trace_id       TEXT NOT NULL,                 -- = task_id
  doc_id         TEXT,
  page_no        INT,
  plugin_id      TEXT NOT NULL,
  plugin_version TEXT NOT NULL,
  capability     TEXT NOT NULL,                 -- pdf.text.extract
  duration_ms    INT NOT NULL,
  bytes_in       BIGINT,
  bytes_out      BIGINT,
  cache_hit      BOOLEAN NOT NULL DEFAULT FALSE,
  cache_miss_reason TEXT,
  created_at     BIGINT NOT NULL
);
CREATE INDEX ix_span_trace ON span(trace_id, created_at);

CREATE TABLE outbox (                           -- ADR-02
  msg_id         TEXT PRIMARY KEY,
  topic          TEXT NOT NULL,
  payload        JSONB NOT NULL,
  created_at     BIGINT NOT NULL,
  published_at   BIGINT
);
CREATE INDEX ix_outbox_unpublished ON outbox(published_at) WHERE published_at IS NULL;

CREATE TABLE consumed_msg (                     -- 消费幂等
  msg_id         TEXT PRIMARY KEY,
  consumed_at    BIGINT NOT NULL
);
```

**关于 `INT4RANGE`**：这是 PG 特性，与 ADR-06 的"标准 SQL 子集"冲突。落地方式：库内用 `page_start INT, page_end INT` 两列，`INT4RANGE` 只作为**生成列 + 排除约束**的辅助（用于保证同文档同引擎的页范围不重叠）。换库时删掉生成列即可，业务查询不受影响。

### 5.3 对象存储 key 规范

```
artifacts/{doc_id[:2]}/{doc_id}/{artifact_id}.json.zst     # 页产物（批级）
artifacts/{doc_id[:2]}/{doc_id}/_index.json                # 批→页范围映射
subsets/{doc_id[:2]}/{doc_id}/gap-{hash}.pdf               # 复杂页子集（4.5.10）
render/{doc_id[:2]}/{doc_id}/p{page_no}@{dpi}.png          # 渲染图（本地盘，非对象存储）
reports/{task_id}/summary.json                             # 总报告（≤10KB）
reports/{task_id}/detail.csv                               # 明细（可筛选）
```

**规则**：key 只由内容推导，不含时间戳与自增序号。`_index.json` 记录"哪些页在哪一个 artifact 里"，是续跑与合并的索引；**必须先写 artifact 再写索引**（PRD 4.8.5 写入顺序）。

**压缩**：`text_items` 用 zstd（压缩比约 4:1，200–400KB → 50–100KB）。压缩在写入前完成，DB 只存 `size_bytes`。

### 5.4 状态与索引策略

- `task.state` / `document.state` 均为**枚举字符串**，不用数字。查日志时不用查字典。
- 历史数据按 `created_at` 月分区（`document` / `page_artifact` / `span`），分区由 janitor 每月创建。
- **不建外键到 `page_artifact`**（写量大、且 artifact 不可变，级联删除无意义），只在 `page_report` 保留逻辑引用。
- `defect.location` 的 JSON 索引只建 GIN（若 PG），换库时降级为全表扫描（量级可控）。

### 5.5 事务边界（决定续跑正确性）

| 操作 | 事务范围 | 顺序 |
|---|---|---|
| 单批完成 | `page_artifact` INSERT + `page_profile` 更新 + `task.resume_point` 更新 + outbox INSERT | **先对象存储写入成功，再开事务**。反过来会出现"索引有、内容无"的幽灵记录 |
| 页质检 | `page_report` + `defect[]` + `page_profile.quality_json` | 同一事务 |
| 修复动作 | `action` INSERT +（若采纳）新 `page_artifact` INSERT | 同一事务；候选被丢弃也要留 `action` 记录（`changed=false`） |
| 文档合并 | `doc_report` + `document.state` | 同一事务；合并产物写对象存储后提交 |
| 任务完成 | `task.state` + `stats` 汇总 | 同一事务 |

**明确的禁止项**：不允许在事务中间调用解析插件或外部服务。所有计算在事务外完成，事务只做写入。

---

## 六、接口设计

### 6.1 插件契约（Python 侧）

```python
# plugins/protocol.py
from typing import Protocol, Sequence
from dataclasses import dataclass

class EnginePlugin(Protocol):
    manifest: dict                            # 见 PRD 5.1

    def classify(self, data: bytes) -> Classification:
        """路由段判定。禁止内部做全量抽取（PRD 5.1 验收 2）。"""

    def extract(self, data: bytes, pages: Sequence[int],
                params: ExtractParams) -> list[PageArtifact]:
        """批级抽取。pages 为 1-indexed，wrapper 负责转换（ADR-07）。
        必须保持入参顺序（PRD 5.1 验收 3）。"""

class ValidatorPlugin(Protocol):
    def validate(self, fv: FeatureVector, ctx: ValidatorContext) -> list[Defect]:
        """只读特征向量，返回缺陷。禁止重解析、禁止改产物（PRD 2.1 铁律）。"""
```

**manifest 校验**在加载时执行，用 Pydantic 模型反序列化，任何字段缺失或不合法直接拒载并告警（不使系统启动失败，ADR-04）。

### 6.2 核心数据结构

```python
@dataclass(frozen=True)
class FeatureVector:
    """L1 的输入。flat dict，便于落库与标定（ADR-09）。
    约 20 个字段，序列化后 < 2KB。"""
    char_count: int
    char_density: float
    glyph_coverage: float
    cid_unmapped_ratio: float
    positioned_ratio: float
    empty_region_ratio: float
    header_footer_ratio: float
    table_count: int
    table_row_variance: float
    reading_order_score: float
    rotation: int
    unmapped_glyphs: int
    # … 新增字段必须同步更新标定集（PRD 4.4.7）

@dataclass(frozen=True)
class Defect:
    code: str                    # 11 个封闭码
    page_no: int                 # 1-indexed
    location: dict               # {type: table|page|bbox, index?, bbox?}
    evidence: dict               # 判定依据的具体数值
    severity: float
    raw_reason: str | None = None

@dataclass(frozen=True)
class PageArtifact:
    artifact_id: str
    doc_id: str
    page_range: tuple[int, int]  # 1-indexed, [start, end)
    engine: str                  # engine.pdf@1.0.0
    params_hash: str
    tier: str
    markdown: str
    text_items: tuple[TextItem, ...]
    tables: tuple[Table, ...]
    reading_order: tuple[int, ...]
    warnings: tuple[str, ...]
```

### 6.3 消息契约（四份，缺一不可）

PRD 2.6 要求契约明确，这里给出 schema 级定义：

```python
# 1) 控制面 → worker：一件活
BatchTask = {
  "msg_id": str, "task_id": str, "doc_id": str,
  "batch_no": int, "page_range": [int, int],      # 1-indexed, [start, end)
  "engine": str, "params_hash": str,
  "affinity_key": str,                            # = doc_id，保证同文档同机
  "resource_hint": "cpu" | "gpu",
  "rulepack_hash": str,
}

# 2) worker → 控制面：一批结果
BatchResult = {
  "msg_id": str, "artifact_id": str, "object_key": str,
  "pages": int, "duration_ms": int,
  "page_reports": [ {page_no, verdict, features, defects[]} ],
  "tier": "T0"|"T1"|"T2"|"T3",
  "warnings": [str],
}

# 3) 质检 → 修复：归因与建议
Attribution = {
  "doc_id": str, "page_no": int,
  "codes": [str], "recommended_tier": "T1"|"T2"|"T3",
  "recommended_ops": [str],
}

# 4) 修复 → 质检：候选产物
OperatorResult = {
  "doc_id": str, "page_no": int, "op": str,
  "candidate_artifact_id": str | None,   # None = 放弃
  "changed": bool, "cost": int, "notes": str,
}
```

**契约的三条硬规则**：
1. 消息体必须是**自包含**的（worker 拿到即可执行，不需要回查控制面）。
2. 消息体**禁止**携带 `text_items`（那是产物，走对象存储）。只带 ID 与指针。
3. 新增字段必须可选且有默认值，否则老版本 worker 会解析失败。

### 6.4 对外 HTTP API

| 方法 | 路径 | 用途 | 备注 |
|---|---|---|---|
| POST | `/v1/tasks` | 创建任务（含 TaskSpec） | 返回 `task_id`；支持 dry-run 预览 |
| POST | `/v1/tasks/{id}/files` | 上传文件（流式） | 单请求 1 文件；服务端算哈希去重 |
| GET | `/v1/tasks/{id}` | 任务状态与统计 | 含 `resume_point`、`gap` 页数 |
| GET | `/v1/tasks/{id}/documents` | 文档列表（分页） | 默认只返回有 gap 的文档 |
| GET | `/v1/documents/{doc_id}/pages` | 页级明细 | 按需调用，不预生成 |
| GET | `/v1/documents/{doc_id}/report` | 单文档报告 | 含缺陷清单 |
| POST | `/v1/documents/{doc_id}/retry` | 重试（指定页/指定档位） | 幂等（同参数返回同结果） |
| GET | `/v1/plugins` | 插件清单与能力索引 | PRD 5.5 |
| POST | `/v1/plugins/{id}/{ver}:{action}` | 启用/禁用/锁版本 | `action ∈ {enable, disable, pin}` |
| GET | `/v1/results/{task_id}/download?fmt=json\|csv\|md` | 下载结果 | 大文件走对象存储预签名 |
| GET | `/status` | 只读状态页（SSR） | 无鉴权（仅本机/内网） |
| GET | `/metrics` | Prometheus 指标 | 供运维抓取 |
| GET | `/healthz` `/readyz` | 存活/就绪 | `readyz` 检查 DB 与对象存储连通 |

**API 设计三条约束**：
- 所有列表接口**必须**支持 `limit`（默认 50，上限 500），禁止返回无界集合。
- 分页用 `limit/offset`（ADR-06），不用游标——数据量在万级，offset 足够。
- 错误响应统一 `{code, message, detail, trace_id}`，`code` 是封闭集，`message` 面向人，`detail` 可含技术信息。

### 6.5 CLI 命令

| 命令 | 作用 | 关键行为 |
|---|---|---|
| `dataagent up` | 拉起全部进程 | 先自检（`doctor` 的逻辑）→ 打印建议配置 → 用户确认 → 起容器 |
| `dataagent down` | 停止（保留数据） | 默认优雅停止：等当前批完成，最长 60s |
| `dataagent status` | 任务与队列概览 | 表格输出，`--watch` 持续刷新 |
| `dataagent logs -f --task <id>` | 跟踪日志 | 按 `task_id` 过滤 structlog 字段 |
| `dataagent plugin list/enable/disable/pin` | 插件管理 | 写入 `plugin_registration`，热生效 |
| `dataagent doctor` | 环境检查 | Docker 版本、端口占用、磁盘、内存、模型文件、字体、DB/对象存储连通性 |
| `dataagent doctor --fix` | 自动修常见问题 | 仅做安全操作（建目录、改权限），不自动改配置 |
| `dataagent bench` | 本机性能基线 | 跑 20 页样本，输出 p50/p95 与建议批大小 |

`doctor` 的检查项必须与 PRD 7.1 #32 对齐：**端口占用、磁盘不足、模型缺失**三类必须能检出。

---

## 七、核心流程设计

### 7.1 任务创建到发布

```
用户 → POST /v1/tasks (TaskSpec)
       ↓
     [校验 TaskSpec schema] → 非法则 400，附字段级错误
       ↓
     创建 task(state=RECEIVED) + 逐文件:
       流式写对象存储(临时) → 算 sha256 → document 登记
       ↓
     [文件体检] 11 项 → health
       ├ rejected → 进待确认队列，document.state=REJECTED，继续处理其他文件
       └ ok/repairable → 进下一步
       ↓
     [一级判定] family（自建，≤50ms）
       ├ pdf → [detect_pdf] 1 次 → 写 PageProfile 前两段 → 分批
       └ 其他 → 直接分批（engine.csv/text/image）
       ↓
     [分批] 32 页/批，OCR 页单独成批 → outbox 批量投递
       ↓
     worker 消费 → ... → 页级 pass/fail → 合并 → 发布
```

**要点**：文件体检与一级判定在**同一个扫描过程**中完成（PRD 4.2.5 规则 3），不重复读文件。

### 7.2 批级解析与 L1（含时间线）

```
批 N（32 页）
  ├ 0. 估算内存：32 × 1.2MB ≈ 38MB，加余量 < 50MB ✓
  ├ 1. 调插件 extract（批量，保持顺序）       ≈ 32 × 16ms = 512ms
  ├ 2. 逐页算特征向量                        ≈ 32 × 0.6ms = 19ms
  ├ 3. 写对象存储（zstd 压缩）                 ≈ 30ms
  ├ 4. 开事务：artifact + profile(后两段) + resume_point + outbox
  ├ 5. 门控判定 → 缺陷
  │     ├ pass → 无事
  │     ├ T1 缺陷 → 同进程跑算子（毫秒级）→ 复检 → 采纳或丢弃
  │     └ T2/T3 缺陷 → 写 PageGap，入队增强（低优先级）
  └ 6. 报心跳（处理到第几页已推进）
```

**为什么 L1 不等全文档**（PRD 4.4.11 的工程理由）：
- 内存：200 页的 `text_items` 常驻是 120–250MB，按批落盘后稳定在 50MB。
- 提前终止：第 3 页发现编码全损，可把后续批直接改派 OCR，省下 197 页的无效解析。
- 填充 IO 间隙：解析是 CPU 密集，L1 是内存计算，同进程交替不争抢。

### 7.3 增强回路

```
def enhance(artifact, defects, budget):
    for d in sorted(defects, key=-severity):
        if d.code in tried: continue          # 同码唯一（PRD 4.5.9）
        tried.add(d.code)
        spec = MAP[d.code]                    # 写死，不推断
        if spec.tier != "T1" and not budget.consume(spec.tier):
            return escalate(artifact, defects)
        r = spec.op(artifact, d, ctx)
        if not r.changed: mark_dead_end(d.code); continue
        if improvement(artifact, r.candidate, d) < 0.05:
            continue                          # 有变化但无进展 → 丢弃
        artifact = r.candidate
        defects = recheck(artifact, d.scope)   # 只复检相关范围
        if not defects: break
    return escalate_with_gap(artifact, defects) if defects else artifact
```

**三层停机 + 同码唯一**（PRD 4.5.9）：单页≤3 轮、累计≤2s、单轮提升<0.05 判无效、同码只修一次。

### 7.4 崩溃恢复

```
t0   worker 处理第 3 批，心跳记录 page_cursor=78
t1   worker 被 kill -9
t2   Redis 消息未 ACK → 进入 pending，超时（默认 60s）后由 janitor 重投
t3   另一 worker 收到 msg → 查 task.resume_point
t4   resume_point.batch_no = 2（第 2 批已提交）→ 从第 3 批开始
t5   已完成的批：产物已在对象存储，索引已提交 → 0 重做
```

**必须满足**（PRD 6.3 验收）：恢复耗时 ≤30s、已完成的批 0 重做、不存在"状态已变但产物读不到"。

### 7.5 复杂页聚包增强（PRD 4.5.10）

```
散落 gap 页（doc A p37, p82；doc B p12…）
  ↓ 按 (engine 需求, 页面特征) 分组
抽子集：用 extract_pages_markdown 产临时 PDF（只含 gap 页）+ 页码映射表
  ↓
聚包：50–100 页/包 → 20 个包（而非 2000 次调用）
  ↓
批量调外部增强器（有界线程池 4）→ 结果带 (doc_id, page_no)
  ↓
回填：替换对应页产物，tier 降级，重算该页 L1
```

**页码映射表是这步的关键**：临时 PDF 的第 3 页 = 原文件第 82 页。映射表随子集一起写对象存储，回填时读取。

### 7.6 换引擎（T3）

```
缺陷 code ∈ {encoding_loss, low_density} 且 T1/T2 均失败
  ↓
按 registry 查 capability=pdf.text.extract 的其他实现（priority 排序）
  ↓
若无 → 查 engine.ocr（需 GPU/PDFium/ORT）
  ↓
ocr 也失败 → PageGap + mark_only 降级（PRD 4.5.11）
```

**成本守卫**：单文档 T3 页数不得超过 20%（默认），超限时停止换引擎并整份标记 `needs_review`——否则成本优势归零。

---

## 八、并发、资源与性能

### 8.1 并发参数

```
worker_进程数 = min(核数 × 1.25, 可用内存 / 单进程上限, 32)
并发批数      = worker_进程数 × 1
队列预取      = worker_进程数 × 4        （超出即背压）
外部调用并发   = 4（默认，可配）
```

| 节点规格 | worker | 批大小 | 单进程上限 | 临时盘 |
|---|---|---|---|---|
| 8C16G | 6–8 | 16 | 1 GB | 20 GB |
| 16C64G | 12–16 | 32 | 1.5 GB | 50 GB |
| 32C128G | 24–32 | 50 | 2 GB | 100 GB |

### 8.2 内存预算核对

```
单批 32 页 × 1.2MB（text_items 内存态）        = 38.4 MB
markdown 32 × 8KB                             =  0.25 MB
中间结构（排序、聚类、压缩缓冲）                 ≈  8 MB
────────────────────────────────────────────
峰值                                           ≈ 47 MB  （预算 50 MB ✓）

200 页全本若常驻                                = 240 MB  （禁止，必须按批释放）
```

**禁止项**：全本合并阶段不得一次性加载 200 页坐标结构（预算 200MB）；必须流式拼接，按页序读、按页写。

### 8.3 关键路径与瓶颈

| 环节 | 单页成本 | 是否关键路径 |
|---|---|---|
| 解析（插件） | 16ms | 是 |
| 特征提取 + 门控 | < 1ms | 是（但与解析同进程，可重叠） |
| 对象存储写 | ~1ms（摊销，32 页一次） | 否 |
| L1 复检（T1 后） | 1–3ms | 仅失败页 |
| L2 抽样 | 100–300ms/页 | 否（15 页/文档） |
| 外部增强 | 秒级 | 否（异步、聚包） |

**端到端 120ms/页的构成**（PRD 6.1）：解析 16ms + L1 1ms + 落库摊销 1ms + 队列/调度摊销 2ms，其余为并发等待与 IO 抖动。**目标留了 6 倍余量**，说明瓶颈不在解析插件。

### 8.4 背压

```
队列深度 > worker × 4        → worker 降低 claim 频率（sleep 递增）
队列深度 > worker × 20       → 停止接受新任务（API 返回 429 + Retry-After）
临时盘可用 < 阈值            → 不接新批（PRD 4.8.6 A）
GPU 队列深度 > 2 × GPU 数    → 降级为 CPU 路径或排队（避免打爆显存）
```

---

## 九、可靠性设计

### 9.1 幂等矩阵

| 操作 | 幂等键 | 重复执行的结果 |
|---|---|---|
| 文件上传 | `sha256(bytes)` | 返回已有 `doc_id`，不重复处理 |
| 解析一批 | `doc_id + page_range + engine + params_hash + rulepack_hash` | 命中已有 artifact，跳过计算（仅 `deterministic` 插件，PRD 5.1） |
| 质检一页 | `artifact_id + validator@version` | 覆盖写 `page_report`，结果一致 |
| 修复动作 | `artifact_id + code + op` | 同一动作不重复执行（同码唯一） |
| 结果发布 | `task_id` | 重复发布覆盖同一对象 key |

### 9.2 故障分类与处置

| 故障 | 检测方式 | 处置 | 影响范围 |
|---|---|---|---|
| worker 崩溃（OOM/段错误） | 心跳 60s 无进展 | 消息重投，从 `resume_point` 续跑 | 一批重做 |
| 单个文件让插件崩溃 | 子进程退出码 | 该批标失败、写 PageGap、ACK 掉消息（防毒丸） | 一批记为 gap |
| DB 连接中断 | 连接池异常 | 重试 3 次（指数退避），失败则进程退出由 supervisor 拉起 | 无（事务未提交） |
| 对象存储不可用 | 写入超时 | 不提交事务，消息 nack 重投 | 一批 |
| Redis 不可用 | 连接失败 | worker 进入等待循环，不退出；janitor 用 PG 兜底重投 | 延迟增加 |
| 外部增强器超时 | 请求超时（默认 120s） | 记为 `fallback.used=timeout`，降级 mark_only | 单页 |

### 9.3 毒丸消息防护

一条消息若被重投超过 **3 次**仍失败，进入死信队列（Redis Stream 的 `dead` 流），并写 `page_gap` 标记 `reason=poison`。**绝不允无限重投**——这是批处理系统最常见的"看起来在跑、实际卡死"。

### 9.4 优雅停止

```
SIGTERM → 停止 claim 新消息 → 等当前批完成（最长 60s）
        → 未完成则提交已完成部分（批是事务边界）
        → 退出码 0
SIGKILL → 什么都不做（设计上已支持）
```


---

## 十、可观测性设计

### 10.1 三条链路必须可追

| 链路 | 载体 | 查询方式 |
|---|---|---|
| 任务级 | `task_id` 贯穿所有日志与 span | `dataagent logs --task <id>` |
| 页级 | `(doc_id, page_no)` 出现在每条相关日志 | SQL 直查 `defect` / `action` / `span` |
| 插件级 | `span` 表：插件 id、版本、耗时、字节数、缓存命中 | SQL 聚合出"哪个插件在拖慢" |

### 10.2 日志规范

```python
log.info("batch.done",
         task_id=..., doc_id=..., batch_no=3, pages=32,
         duration_ms=542, artifact_id=..., tier="T0",
         defects={"table_structure_broken": 2})
```

- 统一 JSON，字段名固定（`task_id`/`doc_id`/`page_no`/`batch_no`/`duration_ms`）
- **禁止**打印文件正文、`text_items`、PII（PRD 5.4）
- 级别约定：`INFO` 状态推进、`WARNING` 可恢复异常与降级、`ERROR` 需要人介入

### 10.3 指标清单（Prometheus）

| 指标 | 类型 | 用途 |
|---|---|---|
| `da_pages_total{family,tier,verdict}` | Counter | 核心吞吐与档位分布 |
| `da_docs_total{family,state}` | Counter | 文档级状态分布（算 STP） |
| `da_gap_pages_total{code}` | Counter | 按缺陷码的 gap 分布（= 归因漂移告警依据） |
| `da_plugin_duration_ms{plugin,capability}` | Histogram | 插件耗时 p50/p95 |
| `da_queue_depth{stream}` | Gauge | 队列积压 |
| `da_worker_up` / `da_heartbeat_age_s` | Gauge | 节点存活 |
| `da_batch_size` | Histogram | 降批是否频繁触发 |
| `da_t1_hit_ratio` | Gauge | **T1 命中率（自建后处理够不够的判据）** |
| `da_t3_ratio` | Gauge | **成本预警核心指标（PRD 4.9.3）** |
| `da_plugin_reason_unknown_ratio` | Gauge | 上游原因码映射表是否过期（PRD 4.5.7） |

### 10.4 告警规则（与 PRD 4.9.3 对齐）

| 告警 | 条件 | 处置建议 |
|---|---|---|
| STP 跌破 | `da_docs_total{state="published"}/total < 0.75` 持续 30min | 查 Top 缺陷码 |
| T3 占比异常 | `da_t3_ratio > 0.15` 持续 1h | 自建后处理覆盖不足，查 T1 映射表 |
| 未知原因突增 | `da_plugin_reason_unknown_ratio > 0.05` | 插件升级导致，补映射表 |
| 队列积压 | `da_queue_depth > worker × 20` 持续 10min | 扩容或降采样 |
| 节点离线 | `da_heartbeat_age_s > 60` | 检查进程/机器 |
| 成本异常 | 单页成本较 7 日均值 +50% | 查是否大量走 T3 |

---

## 十一、安全设计

| 项 | 实现 |
|---|---|
| 上传校验 | 大小上限、magic bytes、扩展名不参与判定；**上传目录不可执行** |
| 路径穿越 | 文件名只用于展示，落盘用 `doc_id`；禁止用原始文件名拼路径 |
| 解压炸弹 | 文件体检第 10 项（流解压比 ≤ 200:1）；渲染前校验页面尺寸（第 7 项） |
| 插件沙箱 | `unsigned` 插件强制容器/子进程，禁网、只读输入、资源限额（PRD 5.5） |
| 凭证 | DB/对象存储密码只从环境变量或 secrets 文件读，不写进 `config.yaml`、不进日志 |
| 日志脱敏 | 结构化字段白名单；异常堆栈中的路径与内容需过滤 |
| API 鉴权 | P0：本地/内网部署，单一 API Key（Header）；P1：用户体系 + RBAC |
| 审计 | 接入/配置变更/规则变更/人工修正/插件启停 全部写 `audit_log`（追加写，不可改） |
| 数据删除 | 按 `doc_id` 彻底删除：DB 行 + 对象存储前缀 + 派生报告，三者同一操作 |
| 传输 | TLS 1.2+（P0 可选自签，P1 强制） |

**一条必须写进代码的约束**：`/status` 页面与 `/metrics` 只允许绑定 `127.0.0.1` 或内网网段，默认不对公网暴露。

---

## 十二、部署与交付

### 12.1 三形态

| 形态 | 命令 | 适用 | 备注 |
|---|---|---|---|
| **Docker Compose**（主推） | `docker compose up -d` | 绝大多数客户 | 单机→多机只改 `.env` 里的后端地址 |
| **pipx 单机** | `pipx install dataagent && dataagent up` | 无 Docker 的开发/个人 | 需 SQLite 降级（不支持并发多 worker，仅演示用） |
| **离线包** | `tar` 含镜像 + wheel + 模型 + 字体 | 政企无外网 | 安装脚本只做 `docker load` + `compose up` |

### 12.2 compose 拓扑（单机）

```yaml
services:
  api:      { image: dataagent:1.0, command: api,      ports: ["8080:8080"] }
  worker:   { image: dataagent:1.0, command: worker,   deploy: { replicas: 8 } }
  janitor:  { image: dataagent:1.0, command: janitor }
  postgres: { image: postgres:16, volumes: ["pgdata:/var/lib/postgresql/data"] }
  redis:    { image: redis:7-alpine, command: ["--appendonly", "yes"] }
  minio:    { image: minio/minio, command: server /data }
```

**多机迁移只改三处**：`db.dsn`、`queue.url`、`storage.endpoint`。代码零改动（ADR-12）。

### 12.3 资源下限

| 项 | 最低 | 推荐 |
|---|---|---|
| CPU | 4 核 | 16 核 |
| 内存 | 8 GB | 64 GB |
| 磁盘 | 50 GB（含临时盘 20 GB） | 500 GB SSD |
| Docker | 24+ | 24+ |

**磁盘必须 SSD 或本地盘**：临时盘放网络盘会让单页耗时从 16ms 涨到 200ms（PRD 4.8.5）。

### 12.4 离线包内容清单

```
dataagent-1.0-offline/
├── images/            dataagent.tar, postgres.tar, redis.tar, minio.tar
├── wheels/            *.whl（含 pdf-inspector、pikepdf、pypdfium2）
├── models/            PP-OCR 模型（仅 P1 OCR 交付）
├── fonts/             常用中文字体（渲染校验用）
├── config.example.yaml
├── install.sh         校验 checksum → docker load → compose up
└── doctor.sh          安装后自检
```

**未做离线包 = 政企第一步就卡住**，这条不是优化项，是交付门槛。

---

## 十三、目录结构与工程规范

### 13.1 目录

```
dataagent/
├── pyproject.toml                 # hatchling + uv lock
├── src/dataagent/
│   ├── config.py                  # pydantic-settings，单一 schema（ADR-12）
│   ├── cli/                       # up/down/status/logs/plugin/doctor/bench
│   ├── api/                       # app.py, routes/, deps.py, templates/status.html
│   ├── core/
│   │   ├── state.py               # 状态机（唯一合法转移表）
│   │   ├── ids.py                 # 内容哈希 / uuidv7
│   │   ├── budget.py              # 三层预算
│   │   └── errors.py              # 错误码封闭集
│   ├── ingest/                    # receive.py, health.py（11 项体检）
│   ├── sniff/                     # pipeline.py, magic.py, encoding.py, delimiters.py
│   ├── route/                     # decision.py, chains.py（链路注册表）
│   ├── profile/                   # writer.py（两段写入）
│   ├── extract/
│   │   ├── wrapper.py             # ★ 唯一页码转换点（ADR-07）
│   │   ├── batching.py            # 分批 + 内存估算 + 降批
│   │   └── artifacts.py           # PageArtifact 编解码（zstd）
│   ├── quality/                   # features.py, gates.py, sampling.py, calibration.py
│   ├── repair/
│   │   ├── operators/             # postprocess.py, reinput.py, rerun.py, engine_swap.py
│   │   ├── mapping.py             # defect_code → operator（写死）
│   │   └── loop.py                # 增强回路 + 停机
│   ├── merge/                     # assembly.py, crosspage.py, doc_checks.py
│   ├── output/                    # json_out.py, csv_out.py, md_out.py, report.py
│   ├── rules/                     # rulepack.py, expr.py（json-logic）
│   ├── plugins/
│   │   ├── protocol.py            # Protocol 定义
│   │   ├── manifest.py            # Pydantic 校验
│   │   ├── registry.py            # 能力索引 / 启停 / 版本锁
│   │   ├── loader.py              # 加载 + 降级
│   │   └── adapters/              # engine_pdf.py, engine_csv.py, engine_text.py, validator_l1.py
│   ├── store/
│   │   ├── db.py                  # SQLAlchemy Core engine（sync + async）
│   │   ├── models.py              # Table 定义（非 ORM 类）
│   │   ├── objectstore/           # base.py, local.py, s3.py
│   │   ├── queue/                 # base.py, redis_streams.py
│   │   └── outbox.py              # 事务性 outbox
│   ├── runtime/
│   │   ├── worker.py              # 主循环
│   │   ├── supervisor.py          # 进程拉起与重启
│   │   ├── heartbeat.py           # 按进展心跳
│   │   ├── resources.py           # 探针 + 建议值 + 守卫
│   │   └── sandbox.py             # 冷路径插件子进程
│   └── observe/                   # span.py, metrics.py, logging.py, audit.py
├── migrations/                    # alembic
├── tests/                         # unit/ integration/ e2e/ fault/ bench/
├── deploy/                        # docker-compose.yml, Dockerfile, offline/, pinned.txt
└── docs/                          # 本方案 + ADR 记录
```

### 13.2 工程规范（可自动化检查的才写进规范）

| 规范 | 检查方式 |
|---|---|
| 页码偏移只允许在 `extract/wrapper.py` | CI grep |
| `worker` 内禁止 `asyncio.run` | CI grep |
| 业务代码禁止直接 `redis.xadd` / `boto3.client` | CI grep（必须经 `store/`） |
| 算子禁止 IO（`open`/`requests`/`db`） | CI grep + 单测打桩 |
| 新增 defect code 必须同步 `mapping.py` | 单测：枚举与映射表长度一致 |
| 新增 FeatureVector 字段必须同步标定集 | 单测：字段集哈希与标定文件一致 |
| `core/` `store/` `repair/` mypy 严格 | CI |
| 所有 DDL 变更必须走 Alembic | CI 检查 migrations 与 models 一致 |

**规范只写"能机器判定的"**。写不进去的（命名、注释风格）靠 review，不占文档。

### 13.3 分支与发布

- 主干开发 + 短分支；合并前必须过 CI（lint / type / unit / 集成）。
- 版本号语义化：`MAJOR.MINOR.PATCH`，`api_version` 与插件契约同步（破坏性变更才 +1）。
- 每个 release 附一份 `CHANGELOG.md`，记录：插件版本 pin 变更、阈值变更、缺陷码增减——这三类是唯一会影响历史结果可复现性的变更。

---

## 十四、测试策略

### 14.1 分层

| 层 | 范围 | 工具 | 目标 |
|---|---|---|---|
| 单元 | 判定器阈值、编码检测、分隔符统计、页码转换、算子幂等、预算扣减 | pytest + hypothesis | 行覆盖 ≥ 80%，核心模块 ≥ 90% |
| 集成 | 一次完整任务（PG/Redis/MinIO 用 testcontainers） | pytest | 覆盖 M1–M3 全部路径 |
| 端到端 | 200 页黄金集，出 STP/TEDS/标题层级 | 自建 eval 脚本 | 对齐 PRD 6.2 |
| 性能 | 单页 p50/p95、批量吞吐、内存峰值 | `dataagent bench` | 对齐 PRD 6.1 |
| 故障注入 | kill -9、断网、磁盘满、毒丸、OOM | 脚本 + chaos 开关 | 对齐 PRD 6.3 |
| 回归 | 插件升级前后产物对比 | 黄金集 diff | 差异必须人工确认 |

### 14.2 六个必须写的测试（对应 PRD 最容易翻车的点）

```python
# 1. 页码基准（PRD 4.3.3 / 验收 #37）
def test_page_index_consistency():
    r = extract_pages_markdown(path, pages=[k])
    assert r.pages[0].page == k
    assert (k + 1 in r.pages_with_tables) or not has_table(k)

# 2. 未知原因不丢失（PRD 4.5.7 / 验收 #38）
def test_unknown_reason_passthrough():
    d = map_defect(reason="brand_new_reason_from_upgrade")
    assert d.code == "plugin_reason_unknown"
    assert d.raw_reason == "brand_new_reason_from_upgrade"

# 3. 算子不修改原产物且幂等（PRD 4.5.8 两条硬约束）
def test_operator_immutable_and_idempotent():
    before = deepcopy(artifact)
    r1 = rebuild_table_cells(artifact, defect, ctx)
    r2 = rebuild_table_cells(artifact, defect, ctx)
    assert artifact == before
    assert r1.candidate == r2.candidate

# 4. 无进展判定（PRD 4.5.9）
def test_gate_after_no_progress():
    r = loop.enhance(artifact, [defect_with_no_improvement], budget)
    assert r.iterations == 1 and r.escalated

# 5. 单批崩溃不影响其他批（PRD 6.3）
def test_poison_file_isolated(worker):
    worker.submit(poison_pdf); worker.submit(good_pdf)
    assert gap_written(poison_pdf) and published(good_pdf)

# 6. 强杀后续跑（PRD 6.3）
def test_resume_after_kill(worker, task):
    worker.run_until(page=100); kill9(worker)
    worker2 = start(); worker2.run()
    assert no_reread_of_batches(0..3) and task.published
```

### 14.3 CI 流水线

```
1. lint + type（ruff, mypy）            ~40s
2. unit（-n auto）                      ~90s
3. integration（testcontainers）         ~4min
4. 构建镜像 + 冒烟（起 compose 跑 1 个小 PDF）
5. 性能基线（仅 main 分支 / nightly）     ~8min
6. 黄金集评测（仅 release 分支）          ~20min
```

**第 5、6 步不进每次 PR**，否则开发者会开始绕过 CI。

---

## 十五、实施计划

### 15.1 里程碑与任务分解（人日，按 2–3 人团队估算）

**M1（3 周）— 能跑通一份 PDF**

| 任务 | 人日 | 依赖 | 产出 |
|---|---|---|---|
| 数据模型 + Alembic 迁移 | 3 | — | 全部表可建 |
| 配置 schema + `config.yaml` | 1 | — | 单一配置（ADR-12） |
| 文件体检 11 项 | 3 | 数据模型 | `ingest/health.py` |
| 一级判定器（六层 + 三态） | 5 | 文件体检 | `sniff/*`，阈值初值 |
| pdf 插件适配 + wrapper（页码唯一转换点） | 3 | — | `extract/wrapper.py` |
| 路由决策 + 分批 | 3 | wrapper | `route/*`, `extract/batching.py` |
| 插件注册表（能力索引/启停/锁版本） | 3 | 数据模型 | `plugins/registry.py` |
| worker 主循环 + 心跳 + 队列 | 4 | 注册表 | `runtime/worker.py` |
| compose + CLI 骨架（up/status/doctor） | 4 | 全部 | 可 `dataagent up` |
| **合计** | **29 人日** | | 2 人 × 15 天 ≈ 30 |

**M2（7 周）— 有质量闭环**

| 任务 | 人日 | 依赖 |
|---|---|---|
| L1 特征提取 + 门控 + 缺陷码 11 项 | 8 | M1 |
| T1 算子库（4 个后处理算子） | 10 | 特征提取 |
| 画像组装（两段写入） | 3 | M1 |
| 增强回路 + 停机 + 预算 | 6 | 算子库 |
| 续跑（outbox + resume_point + 毒丸） | 6 | worker |
| 资源预检 + 估算 + 降批 + 守卫 | 4 | worker |
| L2 抽样 + L3 文档级校验 | 6 | 门控 |
| 合并 + 三档交付 + 报告分层 | 8 | 门控 |
| 集成测试与故障注入 | 6 | 全部 |
| **合计** | **57 人日** | 2 人 × 35 ≈ 70（含 buffer） |

**M3（9 周）— 可验收**

| 任务 | 人日 |
|---|---|
| 黄金集建设（200 页人工标注） | 12 |
| `extract_quality` 标定 + 阈值拟合 | 8 |
| RulePack 引擎（json-logic + 校验器 + dry-run） | 10 |
| 复杂页聚包增强 | 8 |
| 评测脚本（STP/TEDS/标题/阅读顺序） | 6 |
| 性能压测与调优（对齐 6.1） | 10 |
| **合计** | **54 人日** |

**M4（13 周）— P1 分布式**：队列抽象、多节点亲和、OCR 插件、冷路径沙箱（约 45 人日）。
**M5（16 周起）**：P2 界面、MCP 出口、国产化适配。

### 15.2 关键路径

```
数据模型 → worker 主循环 → L1 门控 → T1 算子 → 合并输出 → 黄金集 → 验收
                                    ↘ 续跑 ↗
```

**T1 算子库（10 人日）是全程最大单点**，且它决定 STP 能否达标。排期上必须放 M2 前段，不能压到末尾——它是"要靠真实样本反复调"的工作，不是写完就完。

### 15.3 三条排期纪律

1. **续跑与资源预检必须在 M2 完成，不能推迟**。数小时批处理没有续跑就无法交付，且晚做等于重写调度层（PRD 8.4 已写明）。
2. **黄金集必须在 M3 开始前就绪**。没有真值，T1 的"提升是否有效"无法判定，会陷入无休止的主观争论。
3. **夹具样本要早收集**：至少 20 份真实业务 PDF（含扫描、双栏、复杂表、损坏编码），M1 就需要，不能等到 M3。

---

## 十六、技术风险与技术债

| # | 风险 | 影响 | 概率 | 应对 |
|---|---|---|---|---|
| T1 | `pdf-inspector` 升级破坏产物一致性 | 历史结果不可复现 | 中 | 版本 pin + 黄金集回归（14.1 第 6 层）；升级必须走独立 PR |
| T2 | 表格 cell 重建效果不达 TEDS 0.85 | 核心指标不达标 | **高** | M2 早期先做单算子 spike（50 张真实表格上验证），不达标则调整目标为"原生 0.80 + T1 0.83"并提前与验收方对齐 |
| T3 | `extract_quality` 与人工判定一致率不足 0.85 | 门控失效，STP 无从统计 | 中 | 先做特征筛选（单个特征的 AUC），再拟合；不允许直接上一堆特征做黑盒 |
| T4 | 外部二进制（PDFium/ORT）在 ARM 无预编译包 | 信创交付受阻 | 中（P1） | P1 前完成目标平台自编译验证（PRD R10） |
| T5 | 单机 → 多机时 `doc_affinity` 失效 | 同文档多批分散，性能骤降 | 中 | 亲和锁（Redis SETNX）+ 集成测试覆盖两节点场景 |
| T6 | 报告在千份文件时体量失控 | 客户无法阅读 | 低 | 分层报告 + 默认只出有 gap 的文档（PRD 4.7.5） |

**已知技术债（先记账，不隐瞒）**

| 债务 | 何时引入 | 偿还计划 |
|---|---|---|
| PG 专有：`INT4RANGE`、`JSONB`、`GIN` 索引 | M1 | P2 换库时替换为两列 + 文本 JSON，业务查询不变（ADR-06 已预留） |
| 单一 API Key 鉴权 | M1 | P1 换用户体系 + RBAC |
| 状态页无鉴权 | M1 | 绑定内网 + P2 随正式前端补 |
| `span` 表无分区（初期） | M2 | 数据量到 1000 万行前加月分区（janitor 自动创建） |
| 无多租户隔离 | 全程至 P2 | `owner` 字段已预留，隔离靠行级条件 |

---

## 十七、与 PRD 的差异与待确认事项

这一章是给评审用的。所有差异必须显式列出，**不允许埋在实现细节里**。

### 17.1 已确认按 PRD 执行，无差异

- 进程隔离用多进程（ADR-01 只是实现方式不同，语义一致）
- 注册表做、网关不做（ADR-04）
- 算子同进程（ADR-08）
- 内容哈希主键（ADR-05）
- 页码内部统一 1-indexed（ADR-07）

### 17.2 三处需要你拍板

| # | 事项 | 方案 A（本方案默认） | 方案 B | 建议 |
|---|---|---|---|---|
| 1 | 队列实现 | Redis Streams + 事务性 outbox | PostgreSQL `FOR UPDATE SKIP LOCKED` 直接当队列 | **A**。与 PRD 4.8.7 一致；outbox 已解决双写。若 M4 发现 Redis 运维成本高，B 的切换代价约 2 人日（接口已按最小语义设计） |
| 2 | 单机形态是否保留 SQLite 降级 | 保留（仅演示/单用户，不支持多 worker） | 不保留，单机也必须起 PG | **A 但标注为"演示模式"**。写清"不支持并发写"，避免客户拿它跑生产 |
| 3 | 外部增强器（MinerU/VLM）的接入形态 | 子进程 + HTTP 两种适配器 | 只做 HTTP | **B**（P1 再定）。P0 只需 `fallback.enhancer=local_ocr/mark_only/none`，不引入外部依赖 |

### 17.3 本方案补充、PRD 未写的实现约束（建议回写 PRD 或至少在评审记录里留存）

| # | 补充内容 | 理由 |
|---|---|---|
| 1 | 事务性 outbox | PRD 只说"队列消息可丢、能从 PG 重建"，但未定义"如何区分已投/未投"。outbox 是那个区分依据 |
| 2 | 亲和锁（Redis SETNX + TTL 300s） | PRD 要求"同文档的批落同节点"，但未给机制。单机无需，多机必需 |
| 3 | 毒丸消息 3 次上限 + dead stream | PRD 有"预算上限"，但没有"消息重投上限"。这是另一种无限循环 |
| 4 | T3 单文档页数 ≤20% 的成本守卫 | PRD 有预算概念，未给具体守门值。没有它，成本优势会被"救每一页"吃掉 |
| 5 | `subsets/` 页码映射表随子集落地 | PRD 4.5.10 提到映射表，未说存哪。存对象存储保证多机可回填 |

---

## 附录 A：配置样例

```yaml
# config.yaml —— 单一 schema，单机与多机同构（ADR-12）
server:
  host: 127.0.0.1          # 默认只监听本机，对外暴露需显式改
  port: 8080
  api_key: ""              # P0 单一 Key；空表示不校验（仅限内网）

db:
  backend: postgres        # postgres | sqlite(演示)
  dsn: postgresql://da:da@localhost:5432/dataagent
  pool_size: 10

queue:
  backend: redis           # redis | memory(测试)
  url: redis://localhost:6379/0
  prefetch: 4              # × worker 数
  visibility_timeout_s: 60 # 未 ACK 超时重投（= 判死阈值）

storage:
  backend: s3              # s3 | local
  endpoint: http://localhost:9000
  bucket: dataagent
  local_root: /var/lib/dataagent/objects   # backend=local 时使用
  zstd_level: 3

resources:
  workers: null            # null = 启动自检给出建议值
  batch_pages: 32          # 上限；OOM 时自动降批 32→16→8
  worker_memory_limit_mb: 1024
  temp_dir: /var/lib/dataagent/tmp
  min_free_memory_ratio: 0.20
  min_free_disk_gb: 20

quality:
  profile: quality         # quality | balanced | fast（PRD 4.4.12）
  l2_sample_pages: 15      # 每文档抽样页数；失败页始终 100%
  t1_max_rounds: 3
  t1_max_ms: 2000
  min_improvement: 0.05
  t3_max_page_ratio: 0.20  # 单文档换引擎页数上限（成本守卫）

fallback:
  enhancer: local_ocr      # local_ocr | mineru | mark_only | none
  on_timeout: mark_only

plugins:
  enabled: ["engine.pdf", "engine.csv", "engine.text", "validator.l1"]
  pinned: { "engine.pdf": "1.0.x" }
  priority: { "pdf.text.extract": ["engine.pdf"] }

observability:
  log_level: INFO
  metrics: true
  span_retention_days: 7
```

## 附录 B：新增一个解析插件的最小清单

以"新增 TXT 解析插件"为例，**不允许改路由/调度/质检代码**（PRD 6.4 验收 6）：

1. 实现 `EnginePlugin.extract`（只声明 `text.extract`，不需要 `classify`）
2. 写 `manifest.yaml`：`id / kind / provides / determinism / cost_model / batching`
3. 放进 `plugins/adapters/` 或通过 `plugins.path` 指向外部目录
4. 注册：`dataagent plugin install ./engine_txt`
5. 跑 `dataagent plugin list` 确认能力索引出现 `text.extract`
6. 提交一个 TXT 文件，验证零核心代码改动即可处理

**若这 6 步中任何一步需要改核心代码，说明开闭原则被破坏**——这是 PRD 7.1 #30 的验收方式。

## 附录 C：PRD 验收项 → 测试方法索引

| PRD 7.1 项 | 验证手段 |
|---|---|
| #1 四种接入方式 | 集成测试：文件夹/上传/API/DB |
| #2 边界处理先于解析 | 注入损坏文件，断言插件调用计数为 0 |
| #3 / #3b 一级判定 | 混淆样本集（改后缀/无后缀/歧义/无判据） |
| #4 两级路由可解释 | 断言 `reason` 与 `profile_snapshot` 落库 |
| #5 非 PDF 不经 PDF 插件 | 上传 CSV/TXT，断言 `engine.pdf` span 数为 0 |
| #6 多 capability | 断言 classify 与 extract 分别被调用 |
| #7–#14 兜底与质检 | 固定样本 + 档位/缺陷断言（14.2 #1–#4） |
| #15–#17 规则与合并 | 黄金集 + RulePack 用例 |
| #18–#20 分布式 | 两节点 compose，验证亲和性与故障转移 |
| #21 文件体检 | 注入页树深度 51 层的 PDF |
| #22–#25 缺陷与续跑 | 14.2 #2、#5、#6 |
| #26–#29 交付与报告 | 构造 gap 比例 0/3/8% 三种文档 |
| #30–#33 插件与部署 | 附录 B 六步 + `doctor` 三场景 |
| #34–#36 并发与异步 | 压测脚本（200 并发）、kill -9 恢复 |
| #37 页码基准 | 14.2 #1 |
| #38 未知原因不丢 | 14.2 #2 |

---

## 附录 D：名词对照（PRD 术语 ↔ 代码标识）

| PRD | 代码 |
|---|---|
| 一级判定 | `sniff.detect_family` |
| 二级路由 | `route.decide` |
| 页面画像 | `PageProfile`（`profile.writer`） |
| 页产物 | `PageArtifact` |
| 缺陷码 | `DefectCode` 枚举（`repair/codes.py`） |
| 兜底档位 | `Tier` 枚举 `T0..T3` |
| 增强算子 | `Operator`（`repair/operators/`） |
| 质检器 | `ValidatorPlugin`（`plugins/adapters/validator_l1.py`） |
| 复杂页收口 | `PageGap`（`repair/gap.py`） |

---

**（全文完）**
