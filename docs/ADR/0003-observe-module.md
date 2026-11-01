# ADR-0003：A9 可观测性自建，不引入外部观测平台

- **状态**：Accepted
- **日期**：2026-11-01
- **相关**：A9（T062–T067）、FR-025、PRD 4.9

## 背景

产品要求全链路可观测与审计（FR-025），同时约束是**完全离线私有化、单机多机同构、轻量**。
本机虽已运行 Langfuse（招标项目），但本项目不能依赖它；P0 的模型调用为 0，M3 才最多
3 个任务级调用点，引入 LLM 观测平台不划算。

## 决策

在 `platform/observe/` 自建最小可用观测，全部离线：

- `log.py`：JSON lines 结构化日志 + `ContextVar` span 上下文（trace_id / span_id / parent）；
  `TRACE_FIELDS` 覆盖 PRD 4.9.1。
- `metrics.py`：FR-025 指标口径与采集器；STP 以文档为单位，误放率分母只含人工判不合格样本。
- `audit.py`：接入 / 配置 / 规则变更 / 人工修正四类留痕，自动关联 trace_id。
- `alerts.py`：告警规则为纯函数；`AsyncAlertDispatcher` 独立线程 + 有界队列，队列满丢弃计数，
  **绝不阻塞主链路**。
- 所有落点通过 `MetricsSink` / `AuditSink` / `AlertSink` 抽象注入，P0 使用日志 / 内存实现。

**不引入** Langfuse、OpenTelemetry SDK 或其他外部观测服务。

## 后果

- P0 没有图形化 trace 界面；审计与指标通过日志 / PG / CLI 报告消费。
- 未来若需要外部追踪，只能在 sink 适配层实现（如 OTel exporter、Langfuse exporter），
  核心 pipeline / kernel 不依赖任何观测 SDK。
- 告警旁路丢消息是可接受的：告警不是业务状态源，宁可丢弃也不阻塞处理链路。
