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

### P0-B 功能模块（B1–B9，2026-11-01）

- B1 接入与文件级体检：folder/upload、内容哈希去重、11 项三态体检、待确认队列
- B2 一级格式判定：magic/编码/分隔符五家族三态、后缀不符事件、歧义双跑
- B3 两级路由与引擎接入：engine.pdf / engine.csv / engine.text、批级抽取
- B4 页面画像两段写入：路由段先写、质量段后写、layout 抽取后回填
- B5 质检：L1 全量逐页 + L2 分层抽样 + 三档性能档位 + 先落库再触发下游
- B6 T1 后处理与修复：算子库、reason 映射、增强回路、失败页聚包、降级框架
- B7 合并/交付/报告/输出：跨页表、缺口占位、三档交付、JSON/CSV/Markdown/Excel
- B8 RulePack 与受限表达式：自建 JSONLogic 求值器、版本化/回滚/dry-run、声明式字段提取
- B9 黄金集工具链：六类 200 页、构造样本、对照查看器、标定与评测脚本

### 可复现性基线（P0-B）

- **插件版本**：`engine.pdf@1.0.0`、`engine.csv@1.0.0`、`engine.text@1.0.0`；
  `pdf-inspector==1.25.2`（uv.lock 锁定）；`pypdfium2` 用于隐藏文本层探测
- **阈值**：
  - 体检：单文件 ≤2GB、页树深度 ≤50、单页 ≤14400pt、对象 ≤200 万、
    资源嵌套 ≤30、解压比 ≤200
  - 一级判定：≤256KB / ≤50ms；分隔符一致性 >0.8 且 spread ≤1 → tabular；
    0.5–0.8 → 歧义
  - L1 门控：glyph_coverage ≥0.95、cid_unmapped ≤0.01、empty_region ≤0.30、
    table_cell_fill ≥0.90、reading_order ≥0.88、header_footer ≤0.05；
    char_density 阈值 text/mixed/scanned = 2.0/1.0/0.2
  - L2：quality 15 页 / balanced 5 页 / fast fail+首末页；增强轮次 3/2/1
  - 修复回路：3 轮 / 2s / 质量提升 ≥5% 且缺陷数减少；同码唯一
  - 聚包：50–100 页；交付 gap_tolerance=5%
  - 隐藏文本层：≥50 字 / `U+FFFD` ≤1% / 可打印 ≥95%；
    标题标定：≥30 文本页无标题触发、标题 ≤80 字且不含多个编号标记
- **缺陷码**：11 码封闭集，无增减（见 `contracts/defects.py` 与 `contracts/defect-codes.md`）
- **已知限制**：B9 人工标注（T122–T124）、吞吐/72h 目标硬件压测待完成（见 ADR-0006）

### P0-B 集成（2026-11-02，Phase 11）

- **B-PLAN（`kernel/plan.py`）**：
  - 分支表达式白名单仅统计指标：`stp_rate` / `pages_total` / `pages_processed` /
    `pages_failed` / `gap_ratio` / `avg_extract_quality` / `elapsed_s` /
    `model_calls` / `cost_per_page`；未知字段生成期拒绝
  - guards 默认 `max_model_calls=8` / `max_replans=3` / `max_wall_time_s=3600`
  - 分支只能向前跳转；分支目标为互斥入口，线性推进时跳过
  - 同一 Plan 版本 canonical JSON + SHA-256 指纹一致；重规划版本 +1 且可 diff
- **端到端编排（`pipeline/task_runner.py`）**：
  - 字符密度按 A4 版心近似面积 468 cm² 计算（P1 由真实 bbox 替换）
  - 修复轮次随 stage strategy：quality=3 / balanced=2 / fast=1
  - 缺口页替代原产物进入合并（同页不得同时存在 artifact 与 gap）
  - 同内容文件在任务内复用结果；rejected / pending / duplicate 均进入任务结果
- **性能门禁（`dataagent bench --sample <pdf>`）**：
  - 单页 p50 ≤120ms、p95 ≤250ms；Python 堆峰值 ≤50MB（tracemalloc 代理口径）
  - 文件打开预算 `⌈页数/批大小⌉+2`；批大小 / worker 由资源公式计算
  - OOM 降批链 32→16→8；吞吐 ≥30 万页/日与 72h 泄漏需目标硬件，bench 标记 N/A

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

### P0 修订（2026-11-01，Phase 12）

- **依赖**：新增运行时依赖 `pypdfium2`（PDFium 隐藏文本层探测）；`pdf-inspector==1.25.2` 不变
- **行为**：
  - 扫描件先探测隐藏文本层；质量达标（字符数 ≥ 50、`U+FFFD` ≤ 1%、可打印率 ≥ 95%）
    则复用并标记 `text_layer_recovered`，不送 OCR；不达标才走 OCR
  - `extract_pages_markdown` 因任一 `U+FFFD` 清空的页（`suspected_garbled_text`）
    可在恢复后进入 native 路径；纯扫描页仍走 OCR
  - 病态文档可按需追加一次整本 `process_pdf` 做标题标定；正常文档保持单遍解析
  - 抽取后回填 PageProfile.layout；恢复页不再因 `pdf_type=image_based` 强制 T3
- **阈值**：隐藏文本层质量门为 P0 初值，待黄金集标定（research R7）
- **黄金集**：扫描类拆分 `invisible_text_layer` 与 `pure_scan`；manifest 增加
  `text_layer` / `text_chars` / `bad_ratio`
- 详见 ADR-0005、plan.md「P0 修订」、tasks-p0-b.md Phase 12（T130–T139）
