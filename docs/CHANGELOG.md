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

### 解析质量（2026-11-03）

- **CJK 文本归一**（`pipeline/quality/text_normalize.py`）：
  - 删除汉字间伪空格；保留编号标记后的结构空格与中英文之间的空格
  - 无终止标点的段落行回流；遇终止标点 / 编号 / 列表 / 标题 / 表格边界停止
  - 表格块原样保留；幂等；接入质检阶段与查看器产物
- **自动回归基线**（`tests/eval/regression.py`）：60 页前后对比
  - 伪空格率 20.69‰ → **3.42‰**；硬换行率 0.613 → **0.423**
  - 表格填充/表头/列方差不变；CER 口径改为忽略空白，基线已重建
- **编号/标点错序修复**（`pipeline/quality/numbering.py`）：
  - `2（）` / `（）2` → `（2）`；`（ 2 ）` / `(2）` / `（2)` → `（2）`；
    行首或中文后的半角 `(2)` → `（2）`；表格块不动、幂等
  - 60 页错序编号 23 处 → **0**；编号密度提升，其余指标无回退
- **表格结构契约与坐标建网格（第一版）**：
  - `contracts/table.py`：`TableGrid` / `TableCell`（行列、span、bbox、来源、置信度）
  - `contracts/structure.py`：`DocumentStructure` / `PageStructure` / text/table blocks
  - `pipeline/table/geometry.py`：pdf-inspector 坐标 → 行聚类 → 单元格分组 →
    列对齐与 colspan → TableGrid；混合页只取表格区域，正文行排除
  - 已知限制：跨页表/续页表头、区域切分与少量签名块误判仍需下一轮优化；
    尚未接入页面产物与 L0 Markdown 表的择优路由
- **表格路由（L0/L1 择优）**：
  - `pipeline/table/markdown.py`：Markdown 管道表 → TableGrid（L0）
  - `pipeline/table/quality.py`：cell 填充率 + 表头有效性 + 引擎置信度
  - `pipeline/table/router.py`：逐表配对，L1 领先 >0.05 才替换 L0；
    有 L0 时多余 L1 区域视为同表碎片，不重复输出
  - 真实页结果：p60 选坐标网格（0.829 vs 0.664），p25/p86 选 Markdown
- **表格区域连续性调优**：
  - 行聚类容差提升到 1.0×文本高度，合并单元格内换行（约 7pt）不再拆成新行
  - 区域垂直间距阈值收紧到 1.8×中位行高，避免把邻近正文/表单并入表格
  - 允许单单元格行留在表内（合并单元格/跨行文本），p25 由 2 块碎片合成 1 块
  - 仍存在的限制：多行单元格之间的逻辑行边界无法仅靠 y 间距判断；
    下一步改用 L0 Markdown 表结构作为锚点做行列投影
- **表格列锚修正**：
  - 列起点取自“列数最多的最宽行”，表头与数据列起点不同不再生成重复列
  - 单元格按左端就近归列，colspan 按右缘覆盖列判断
  - 实测：p86 由 6 列恢复为 3 列，p25 由 10 列降为 5 列，
    p60 路由选择坐标网格（0.829，fill 0.79）
- **L0 锚点投影与空间配对**：
  - `pipeline/table/anchor.py`：用 Markdown 表的逻辑行/单元格文本匹配坐标项，
    回填表与单元格 bbox，覆盖率作为置信度
  - 路由不再按出现顺序配对，改为按 bbox 重叠（较小面积重叠比例）配对；
    未配对 L1 若与任一 L0 区域重叠则视为碎片丢弃，否则保留为独立表
  - 实测：p25 正确输出“上方董事表 + 下方股东大会表”两张表；
    p60 选中 7×4 坐标网格（fill 0.79）
- **页码基准修正与表格对照**：
  - 修正 `pdf-inspector.TextItem.page` 为 1-indexed（此前 +1 导致 L1 与 L0
    相差一页，是 p25 错位/配错表的根因）
  - 新增 `pipeline/table/render.py`（TableGrid → Markdown）与
    `scripts/compare_tables.py`（原页图 vs 选中输出；后由
    `scripts/validate_tables.py` 的四栏对照取代）
  - 对照发现：p25 董事表多行单元格/rowspan 被合并（王鑫刘世仲、男男）；
    p60 只选到长表的下半段；p86 部分行被合并，其余表基本正确

### 表格抽取（2026-11-03）

- **pdfplumber 有框线表格 L2**（`platform/plugins/adapters/table_pdfplumber.py`）：
  - `lines` 策略读取真实单元格边界（lines + rects 均视为边），
    合并单元格按 bbox 跨行/跨列推断 `rowspan/colspan`
  - pdfplumber 左上原点坐标转换为内部左下原点；退化表（<2 行/列、非空格 <2）丢弃
  - 依赖 pin：`pdfplumber==0.11.10`（uv.lock 锁定）
- **验收网关**（`pipeline/table/acceptance.py`）：有框线结果必须通过才允许替换 L0
  - 非空占比 ≥ 0.15；L0 文本字符覆盖率 ≥ 0.85（按表格区域计算，排除标题行）；
    pdfplumber 行数 / L0 行数 ≥ 0.50；多值单元格（多个日期/金额）拒绝；
    bbox 超出页面 5% 拒绝
  - 任一失败回退 L0，并返回 `rejected_ruled` 原因，便于观测
- **路由**（`pipeline/table/router.py`）：有框线候选优先；通过后抑制重复 L0
  （字符覆盖 ≥ 0.8）与重叠 L1 碎片；失败时保持原 L0/L1 逻辑
- **离线验证**（`scripts/validate_tables.py`）：构造样本真值 cell F1 +
  真实页四栏对照（原图 / L0 / L1 / pdfplumber）与验收统计
  - 实测四页（p24/p25/p60/p86）全部选中 pdfplumber：p25 人名/性别/年龄/日期拆开、
    p60 恢复完整 48 行、p86 五张表各自正确；p24 标题行被排除
  - 构造 cross_page 样本 F1 0.9917：夹具框线比文本行少一条，仅少一行表头，
    数据行全部命中

### M3 任务级模型（2026-11-03）

- **NL → Plan**（`kernel/policies/model_plan.py`）：
  - 模型客户端通过 `PlanDraftModel` Protocol 注入（provider 可替换）
  - 输出必须通过 `parse_plan` 校验：3 种 stage kind、统计指标白名单、分支前向跳转
  - 非法输出重试（默认 2 次），每次尝试计一次模型调用；接入 `PlanGuardTracker`
  - `compile_frozen` 返回冻结 Plan；`compile` 提供 PlanPolicy 最小视图
  - 真实 provider（pydantic-ai）适配器待模型端点配置后接入

### 可复现性基线（P0-B）

- **插件版本**：`engine.pdf@1.0.0`、`engine.csv@1.0.0`、`engine.text@1.0.0`；
  `pdf-inspector==1.25.2`、`pdfplumber==0.11.10`（uv.lock 锁定）；
  `pypdfium2` 用于隐藏文本层探测
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
  - 表格指标接入 L1：从产物 Markdown 解析管道表，计算 `table_cell_fill`
    （非空 / 最大列数×行数）、`row_col_consistency`（每行列数方差）、
    `table_header_ok`；填充率 <0.90、列方差 >1.0 或表头缺失 →
    `table_structure_broken`（缺口页进人工复核）
  - 跨页表续页表头恢复：仅当上一页表格触底、当前页表格位于页首且列数一致时，
    沿用上一页表头；缺上下文不猜测。`OperatorContext` 新增 `previous_artifact`，
    `run_enhancement` 支持注入算子上下文
  - TaskRunner 全链路观测：任务 → 接入 → 抽取 → 质检 → 合并 → 输出的 span 链；
    任务结束发指标快照（处理量 / STP / 档位分布 / p50·p95 / 复杂页占比）、
    接入审计（`AuditAction.INGEST`）与告警评估（告警走旁路 sink）
  - `configure_logging` 显式重新启用 logger；Alembic `fileConfig` 改为
    `disable_existing_loggers=False`，迁移不再吞掉应用日志
  - 任务进度落库：`task_progress` 表（迁移 0007），runner 在任务开始 /
    每文档完成 / 任务结束低频 upsert；`/status` 与 `dataagent status`
    经 `SqlStatusProvider` 只读查询；写失败只记日志、不影响主链路；
    worker 仍无状态（状态在 PG，不在进程内）
  - 队列深度 = Redis Streams 未投递（lag）+ 已投递未确认（PEL）；
    worker 存活数来自 Redis 心跳键（TTL 120s，60s 内有心跳计存活），
    worker 每个 poll 周期 best-effort 上报，进程退出后 key 自然过期
  - 执行由 `Supervisor` 驱动唯一状态机：任务级投影
    `RECEIVED → SNIFFED/CLASSIFIED → ROUTED → PARSING → PARSED →
    POSTPROCESS → L1/L2 → MERGED → L3 → PUBLISHED`；全失败 → `ESCALATED`，
    无可用文档 → `REJECTED`。进度 detail 携带 `machine_state`
  - 状态与续跑落 PG：迁移 `0008_task_state` 保存状态机状态、resume_point、
    输入签名与已完成文档结果；相同输入重跑跳过已完成文档（0 重做），
    输入集合变化则整体重跑；带 Plan 的任务暂不续跑（重规划语义待接）
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
