# Changelog

所有**影响可复现性**的变更必须记录在本文件。三类变更必须显式列出：

1. **插件版本 pin**：`id@version`（参与幂等键与结果缓存键）
2. **阈值**：档位 / 重规划 / 告警等规则阈值
3. **缺陷码增减**：封闭集只能随版本升级增减，必须同步 `contracts/defects.py` 与映射表

格式参考 Keep a Changelog；版本号遵循语义化版本。

## [0.1.0] - 2026-11-01

### 新增

- P0-A 基础支撑层（A1–A9）：
  - A1 工程底座：5 层包结构、uv 锁定、CI 门禁、TDD 基建
  - A2 核心机制：状态机唯一转移表、内容哈希 / uuidv7、错误码封闭集、三层预算、单一配置 schema
  - A3 全局共享：页码唯一转换点、缺陷码封闭元数据、算子映射写死
  - A4 存储抽象：对象存储 local/S3、Redis Streams 队列、SQL Core + Alembic
  - A5 插件注册表与加载器：manifest 契约、能力索引注册表、热/冷路径加载、子进程沙箱
  - A6 续跑与资源预检：supervisor 状态机驱动、批级事务、进展心跳、资源建议 / OOM 降批、进程池
  - A7 交付骨架：CLI、只读 `/status`、worker 空壳、Docker Compose / 离线包骨架
  - A8 决策点策略化：8 个 Policy Protocol、页级确定性规则、任务级阈值规则、模型实现占位
  - A9 可观测与审计：JSON 日志 + span、FR-025 指标、审计留痕、旁路告警

### 可复现性基线（P0-A）

- **插件版本**：`engine.pdf` 仅落地契约（api_version=1），具体实现版本在 P0-B 接入时 pin；
  `plugins/manifest` 的 `id@version` 参与幂等键与缓存键
- **阈值**：
  - `TierPolicy`：τ0 = 0.85、class_confidence = 0.85、glyph_coverage = 0.95、header_footer_ratio = 0.05
  - `ReplanPolicy`：失败页占比 ≥ 0.40 → 整本 OCR；`max_replans` = 3
  - `StrategyPolicy`：class_confidence < 0.85 或失败页 ≥ 0.40 → 整本 OCR；预算耗尽 → ABORT
  - 告警：STP < 0.80、误放率 > 0.02、成本涨幅 > 0.20、T3 占比 > 0.20、队列 > 1000、
    心跳 > 60s、失败分布漂移 > 0.20、`plugin_reason_unknown` > 0.05
  - 资源：批大小 / worker 数 = `min(核数×1.25, 可用内存/50MB, 32)`；OOM 降批 32→16→8
- **缺陷码**：11 码封闭集，无增减（见 `contracts/defects.py` 与 `contracts/defect-codes.md`）
