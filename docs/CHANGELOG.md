# Changelog

所有**影响可复现性**的变更必须记录在本文件。三类变更必须显式列出：

1. **插件版本 pin**：`id@version`（参与幂等键与结果缓存键）
2. **阈值**：档位 / 重规划 / 告警等规则阈值
3. **缺陷码增减**：封闭集只能随版本升级增减，必须同步 `contracts/defects.py` 与映射表

格式参考 Keep a Changelog；版本号遵循语义化版本。

## [未发布]

### 表格/文本质量缺陷修复（2026-11-07）

- **伪表格降级**：不再跳过有坐标项的页面；判据改为“表格覆盖页面 ≥60%
  + ≥2 个带句末标点的散文单元格，或任一 ≥120 字散文单元格”。
  修复 R-0033（整页正文被识别成 2×7 表）与 R-0036（无表页生成 3 列表）。
- **数字错序**：行聚类新增“短数字按列间隙回挂”，修复 R-0023 的
  `1 答：（）` 与 `造 21 8 成…`；文本页出现可疑数字串 / `答：空括号` 时
  按需补探坐标项；编号修复新增 `digit + 答：（）` 模式。
- **可观测性**：新增 `table_route_summary` / `table_routed` /
  `table_l0_prose_demoted` / `position_probe_triggered` 结构化事件，
  表格来源、拒绝原因、质量分与降级决定可直接从日志追溯。

### 标注查看器对齐最终交付物（2026-11-07）

- `prepare_review.py` 不再直接展示 pdf-inspector 原始 Markdown，而是跑当前流水线，
  按页展示最终交付物（HTML 表格 + 结构 JSON）；缺口页展示交付占位。
- 每页记录 `artifact_hash`，导出 CSV 新增该列，分数与具体产物版本绑定。
- 第一批 60 页新旧对照：首次生成时 31 页不变 / 29 页变化；表格与数字缺陷修复后
  重新生成为 29 页不变 / 31 页变化（以 `review_rescore` 当前 31 页为准）。
- 新增 `review_final/` / `parsed_final/`（第一批最终产物）与更新后的
  `review_v2/` / `parsed_v2/`；旧 `review/` / `parsed/` 保留为原始抽取基线。
- `calibrate.py` 增加标签与产物版本校验：标签哈希不匹配（或旧标签对应产物已变化）
  时输出 `stale_labels` 并拒绝出权重，避免“旧分数 + 新产物”混算。

### 质量分口径更新（B9 A 路径，2026-11-06）

- **权重变更**：`extract_quality` 改为黄金集拟合权重
  （glyph 0.249 / char_density 0.200 / clean_spacing 0.143 / prose 0.112 /
  numbering 0.146 / table_cell_fill 0.150）；新增文本质量三分量；
  非表格页的表格分量取中性 1.0；cid / reading_order / bbox 等未产出指标权重暂 0。
- 新增 `pipeline/quality/text_metrics.py`：生产 L1 与黄金集标定共用同一文本质量口径
  （clean_spacing / prose_ratio / numbering_ratio / paragraph_flow / heading_ratio /
  line_length_variety）。
- 标定脚本 `tests/eval/calibrate.py` 改为按生产分量拟合并输出全量权重；
  工作点阈值 0.43（全量一致率 0.9167）。
- 60 页实测：生产质量分与人工总分 Spearman 0.155 → 0.488（CV 0.598）、
  needs_manual 一致率 0.917；QC precision/recall 0.667/0.286，门禁仍未达
  （见 `docs/b9-calibration-diagnosis.md`）。

### 规模与中断恢复验证（PR-4-4，2026-11-06）

- 新增任务级强杀续跑演练 `tests/integration/test_resume_kill.py` +
  `scripts/resume_demo_task.py`：真实子进程 + PG 状态存储 + 流式落盘；
  首个文档完成并记录后强杀，重启后已完成文档产物不被重写（哨兵校验）、
  其余文档续跑，收尾 manifest 与落盘产物对账通过。
- 新增 `verify_manifest(output_dir)`：双向对账（清单里的 md/json/tables key
  必须有文件；交付目录下的产物必须被清单引用）；脚本
  `scripts/verify_manifest.py` 供运维直接执行。
- `scripts/e2e_p0.py` 额外输出本机短样本吞吐（明确非目标硬件验收值）。
- 目标硬件项（16C64G 连续 1h ≥30 万页/日、72h 泄漏 <5%）仍需在交付硬件执行，
  见 `docs/scale-validation.md`。

### 交付目标扩展（PR-4-3，2026-11-06）

- 新增 `pipeline/output/targets.py`：交付目标注册表 + P0 确定性 NL 路由。
  名称/别名忽略大小写与空白子串匹配、最长匹配优先；未指定、`no_match`、
  `ambiguous`、`unreachable` 一律回退默认输出根并给出原因。
- `create_builtin_runner(..., deliver_to=..., deliver_targets=...)`：任务创建注入点
  解析交付位置；命中写 `delivery_target_routed`（INFO），回退写
  `delivery_target_fallback`（WARNING，含 `requested/target/reason/detail`）。
- 配置新增 `deliver_targets`（name → path / aliases）；NAS 优先 OS 挂载 +
  路径指挂载点，凭证归挂载层。新增 `docs/delivery-targets.md`（NFS/CIFS 挂载、
  Docker bind mount、路由规则、已知限制）。
- 直接 WebDAV/SMB 后端插件未实现，按需再做。

### 交付访问层（PR-4-2，2026-11-05）

- 新增 `GET /download/<relative_key>`：按 manifest 的相对 key 下发单文件。
  key 解析统一在 `apps/api/downloads.py`，拒绝绝对路径、`..`、反斜杠与控制字符；
  解析后（含符号链接）必须仍在输出根内。
- 新增 `GET /download/zip?keys=a,b`：按需流式 zip，边读边压边传、不落盘、
  无 `Content-Length`；任一 key 非法 400、缺失 404（在开始流式前判定）。
- 新增 `GET /manifest`：下发 `manifest.jsonl`（`application/x-ndjson`）。
- API 经 `output_dir` 注入输出根（`Settings.output_dir`，默认 `var/output`）；
  未配置时下载端点 503，`/health`、`/status` 不受影响。
- compose 的 api / worker 共享 `outputs` 卷挂到 `/app/var/output`。

### 交付产物阶段一（PR-1，2026-11-05）

- 表格择优接入主链路：抽取侧坐标项（按 `pages_with_tables` 门控）与结构元素旁车，
  `PageArtifact` 契约冻结；统一行聚类建块 + 表格槽位 + bbox 吞并安全阀（<60% 页面积）。
- Markdown 交付改为块渲染：表格 HTML 内嵌（rowspan/colspan 无损）+ 表题行 +
  `<!--table:tNNN-->` 锚点；表 ID 文档内唯一。
- L0 伪表格降级为正文（首行长句 + 句末标点）；跨页重复 `<u>` 页眉剔除。

### 质量口径修复（PR-3a，2026-11-05）

- **阈值变更**：`TABLE_STRUCTURE_BROKEN` 的填充率判定由「`table_cell_fill` < 0.90」
  改为「`table_cell_fill` < 0.90 **且** 行列占用率 < 0.8」；表页字符密度阈值
  由页型阈值改为 `min(页型阈值, 0.5)`（P0 初值，待 B9 标定固化）。
- 60 页黄金集双口径对照：旧口径 41 页失败 → 新口径 24 页；17 页缺口转通过，
  0 页反向变差。
- 真实 143 页年报：缺口页 99 → 21，交付表格 53 → 235；p24/p25/p60/p86 进入交付。

### 逻辑行合并（rowmerge，2026-11-05）

- 新增 `pipeline/table/rowmerge.py`：按 rowspan 证据把「一条记录被内部横线拆成多个
  物理行」合并回逻辑行；表头行原样保留；含跨列（`colspan > 1`）的行不参与合并。
- 接入建树：选中网格入槽前应用；茅台 p25 人员表由 21 个物理行合并为
  **14 行（1 表头 + 13 人）**，王鑫/刘世仲等拆分行恢复为独立记录。
- 表头 rowspan 跨入数据行时在表头边界截断，避免合并后单元格重叠。
- `scripts/validate_tables.py` 增加“逻辑行合并”第五栏对照。

### 结构树 JSON 与双视图（PR-2，2026-11-05）

- `contracts/structure.py`：`TextBlock` 增加 `block_id` / `level`；`TableBlock` 增加
  `block_id` / `table_id` / `caption` / `rows_text`；文档内 block_id 与 table_id 唯一性校验。
- 新增 `pipeline/output/structure_view.py`：内部块 → `DocumentStructure`；`rows_text`
  按行序列化（单元格内换行折叠为空格）；输出 `{meta, structure}` JSON 与结构 md 视图。
- 交付切换：JSON 写结构树（`meta` 含交付等级/缺口页/校验），md 由结构渲染；
  md 中每个 `tNNN` 与 JSON 表格块一一对账。
- 真实年报：122 个非缺口页、235 张表、21 个缺口页；md/json 表 ID 完全一致；
  p25 人员表 `rows_text` 13 行（每行一人）。
- 暂缓：`_normalize_headings` 迁入结构树、流式交付（PR-2b）。

### 流式交付与标题迁移（PR-2b，2026-11-05）

- `DocumentDeliveryService.finalize` 每文档合格即写盘 md/json；任务收尾不再统一写；
  `DocumentContext.release_payload()` 在写盘后释放 merged / blocks / structure / meta / layouts。
- `_normalize_headings` 从 `merger.py` 迁入 `build_structure`：merger 退化为纯页序/缺口组装；
  真实年报 254 个标题的层级/文本序列与迁移前完全一致。

### 跨页表网格级合并（PR-3-1，2026-11-05）

- `contracts/table.py::TableGrid` 增加 `page_span`（默认 `(page_no, page_no)`；
  起始页必须等于 `page_no`）。
- 新增 `pipeline/structure/crosspage.py`：相邻页边界、同列数、表头相似 ≥0.8 时按
  **TableGrid 行追加**合并；续页表格块移除；`page_no` 记起始页、`page_span` 记
  （起始页, 结束页）、`bbox` 取起始页。
- `merger.py` 移除文本级跨页表合并，退化为页序/缺口组装；旧跨页测试迁移为网格级测试。
- 触发条件与旧文本口径一致（上页最后一块为表、本页第一块为表）；构造样本页首有标题时
  两版均不合并。

### L1 表格口径切到 TableGrid（PR-3-2，2026-11-05）

- `table_metrics.py` 新增 `grid_table_metrics` 与共享 `table_structure_ok`；
  L1 的填充率/方差/占用率常量收敛到该模块，避免双表漂移。
- 交付期对“仅 `table_structure_broken`”的缺口页用最终 TableGrid 复核：
  填充率/行列占用率/表头通过则移除缺口、恢复交付，并把更新后的 `PageReport`
  写入质检结果存储；有其他缺陷的页仍保留缺口。
- 真实年报：缺口页 21→12（恢复 8/12/14/34/35/41/54/62/125），表格 235→244；
  md/json 表 ID 一致。
- 已知：恢复页的 `extract_quality` 沿用旧 L0 值（保守），恢复判定只清理
  `TABLE_STRUCTURE_BROKEN`。

### tables.json 与分桶落盘（PR-3-3，2026-11-05）

- `TaskConfig.emit_tables_json`（默认关）：可选第三产物 `<doc>.tables.json`，
  仅表格块（`table_id/block_id/page_no/page_span/bbox/caption/rows_text/grid`）。
- 落盘布局切到契约：`md/<hash 前2位>/<hash>.md`、
  `json/<hash 前2位>/<hash>.json`、`tables/<hash 前2位>/<hash>.tables.json`；
  csv/excel 保持扁平派生视图。
- 真实年报：md / json / tables 均为 **244 个表 ID** 且完全一致；开关关闭时不生成
  `tables/` 目录。

### 交付清单 manifest（PR-4-1，2026-11-05）

- `TaskConfig.emit_manifest`（默认关）：任务完成后在输出根目录生成
  `manifest.jsonl`，一行一文档：`doc_id / status / delivery_level / pages /
  gap_pages / md / json / tables / path / note`。
- 产物 key 为相对输出根的正斜杠路径（如 `md/52/<hash>.md`），供程序批量遍历、
  下载与落盘对账。
- 真实年报：1 行清单，md/json/tables key 与分桶产物一致。

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
