# 质量修复计划（质量归因整改）

**日期**：2026-11-08
**来源**：B9 标注审计（`docs/b9-calibration-diagnosis.md`）与表格/归一化代码审计
**状态**：Phase 1–3 与 Phase 4（含 4.3/4.4）已完成（2026-11-08）；Phase 5–7 待实施
**原则**：每个 Phase 独立可交付；先测试后改；全量门禁保持绿；阈值集中、不新增散落常量；
单文件 ≤500 行；所有修复可回滚（按 Phase 单独提交）。

## 0. 目标与非目标

**目标**

1. 标题层级同级一致、跨页稳定，不再出现 `# 198.` / `## 199.` 这类错误。
2. 正文、坐标路径、表格单元格三条输出路径使用同一套确定性归一化。
3. 消除已知误判缺口（R-0002 / R-0077 / R-0098 / R-0102），保留真实缺口（扫描页）。
4. 表格检测以 pdf-inspector 页级先验 + pdfplumber 结构抽取为主，减少自建几何与散落阈值。
5. 拆分超长模块、去重工具、治理硬编码，恢复可维护性约束。

**非目标**

- 不在本计划内实现 OCR（纯扫描页仍按 `needs_ocr` 缺口处理）。
- 不整体替换 pdf-inspector，不改变交付格式（Markdown + JSON）。
- 不做语义模型推断标题；只做确定性规则与已有信号对账。
- 不在本计划内完成 60/120 页人工重评；重评作为修复后的验收动作单独执行。

## 1. 顺序总览

| 顺序 | Phase | 级别 | 目标 | 预估 |
| --- | --- | --- | --- | --- |
| 1 | 标题层级 | P0 | 同级一致、跨页稳定、tagged 角色可用 | M |
| 2 | 表格单元格归一化 | P0 | 单元格空格/换行不再原样进入 HTML | S |
| 3 | 统一归一化入口 | P0 | Markdown / 坐标 / 表格三路径一致 | M |
| 4 | 表格策略与缺口误判 | P0/P1 | 假表、假缺口、页级先验与验收 | L |
| 5 | 结构拆分与去重 | P1 | `build.py` 拆分、工具/常量去重、StrEnum | M |
| 6 | 观测与回归 | P1 | 缺口/标题/归一化决策可追溯 | S |
| 7 | 残留质量 | P2 | 段落切分、页眉页脚、扫描页标签 | M |

> 若优先修“用户可见的缺口页”，可把 Phase 4.1/4.2 提前到 Phase 1 之前执行；
> 本计划按既定顺序编排，Phase 之间无强代码依赖。

## 2. Phase 1：标题层级（P0）

**问题**

- `structure_view.build_structure` 的 `previous_level=0` + `min(level, previous_level+1)`
  把首个 `##` 提成 `#`，同级后续仍是 `##`；`previous_level` 跨页累计。
- R-0023：pdf-inspector 原始为 `## 198.` / `## 199.`，交付为 `# 198.` / `## 199.`。

**改动范围**

- `pipeline/output/structure_view.py`：移除首标题强制提升；引入文档级标题规范化。
- `pipeline/structure/build.py`：标题来源增加中文编号信号（与 `repair/layout.py` 共用规则）。
- `pipeline/repair/layout.py`：把 `normalize_layout` 的中文编号层级重建接入主链路。
- `platform/plugins/adapters/heading_calibration.py`：与主链路层级规则对齐，避免二次改级。
- `PageLayout.roles`：tagged PDF 的 `H1–H5` 经 MCID→文本映射后作为对账信号（不直接覆盖）。

**实施要点**

1. 层级规则：
   - `第X章/编/部分` → 1；`第X节` / `一、` → 2；`（一）` → 3；`1.` / `（1）` → 4。
   - 同一编号模式在同一文档内必须同层；同级标题不得被首个标题提升规则改写。
2. 归一化只在“跳级”时下调（如 H4 → 上一级+1），不做无条件提升。
3. tagged PDF：`StructureElement(page, mcid, role)` 通过 `PositionedText.mcid` 映射到文本；
   角色缺失或冲突时以“编号语义 + Markdown 层级”为准。
4. 记录 `heading_decision` 观测事件：page、text、source（numbering/markdown/structure）、
   level、reason。

**测试与验收**

- 新增单测：R-0023 原始 `##/##` → 交付同级；`一、/二、` 同级；`第X章/第X节` 父子关系；
  首个标题不提升；跨页连续标题不互相压低；tagged 角色 MCID 映射样例。
- 黄金集 60 页标题层级一致性对比：同级编号同层率、父子关系合法率。
- 门禁：全量测试、ruff、mypy、import-linter、check-gates。

**风险**

- 层级变化会改变 JSON 结构树与 Markdown 标题，需重跑交付对账。
- tagged 角色覆盖不全（茅台第 5 页无 H 角色），不能被当作唯一真值。

**实施结果（2026-11-08）**

- 规则落在 `contracts/headings.py`（`classify_numbering` / `numbering_level` /
  `level_from_role`）：platform 不能依赖 pipeline，规则必须下沉契约层才能被
  `heading_calibration` 复用。
- 新增 `pipeline/structure/headings.py::HeadingLevelNormalizer`；
  `build_structure` 移除 `previous_level=0` + `min(level, prev+1)`，改由它归一化。
- 建块（markdown 回退 + 坐标项两条路径）新增编号标题来源与 H1–H6 角色兜底。
  与计划的差异：`（一）` 与 `1.` **只提供层级、不独立建标题**——真实语料中
  `（一）` 是条文内枚举项（招标投标法 p1 实证），提升会造成整页假标题。
- 与计划的差异：未把 `repair/layout.py::normalize_layout` 整体接入主链路，
  其伪表格解包会与 `build.py::_is_prose_table` 重复（属 Phase 4）。只把编号
  层级规则抽到契约层，两边共用。
- `heading_calibration` 注入标题时按编号语义对齐层级。
- 真实验证：R-0023 `## 198.` / `## 199.` 同级（原 `# 198.` / `## 199.`）；
  招标投标法 `第一章` / `第二章` 均 H1；国务院意见 `一、`…`七、` 均 H2。
- 遗留：`PageLayout.roles` 仅作“非标题行的兜底来源”，尚未做全量对账；
  `heading_decision` 观测事件按计划留到 Phase 6。

## 3. Phase 2：表格单元格归一化（P0）

**问题**

- `normalize_cjk_text` 遇到 `|` 开头的表格块整块跳过；`repair_numbering` 跳过表格行。
- pdfplumber 单元格只做 `strip()`，单元格内伪空格/硬换行直接进入 HTML 与 `rows_text`。

**改动范围**

- `pipeline/quality/text_metrics.py` 或新增 `pipeline/quality/cell_normalize.py`：
  提供 `normalize_cell_text(text)`（去 CJK 伪空格，保留单元格内换行语义）。
- `pipeline/table/html.py` / `pipeline/output/structure_view.py`：
  在渲染边界统一调用；`rows_text` 与 HTML 同源。
- `platform/plugins/adapters/table_pdfplumber.py`：不在适配器内做文本策略，保持抽取与渲染分离。

**实施要点**

- 单元格内换行保留（HTML `<br>`），但行尾硬换行可按规则合并；不得跨单元格合并。
- 幂等；表格结构（row/col/rowspan）不变，只改文本。
- 对 L0 Markdown 表与 pdfplumber 表使用同一函数。

**测试与验收**

- 单测：`中 文` → `中文`；单元格内两行合并/保留规则；HTML 文本与 `rows_text` 一致；
  真实样本 R-0041 表格文字无伪空格，结构不变。
- 门禁全绿；JSON 仅文本字段变化，结构字段不变。

**实施结果（2026-11-08）**

- 原语放在 `pipeline/quality/text_normalize.py`（与正文归一同一职责），未新建
  `cell_normalize.py`；Phase 3 的 `normalization.py` 可直接复用这两个入口。
- 与计划的差异：归一化落在建块槽位（`build.py::_build_slots`），不在 HTML /
  `rows_text` 渲染边界。理由：渲染层归一化会让 `tables.json` 与 grid 保留原文，
  且 Phase 3 明确要求“禁止在渲染层再补规则”。一个网格汇聚点即可保证
  md / json / tables.json / rows_text / HTML 同源。
- 顺序有意为之：先 `normalize_table_grid` 再 `merge_logical_rows`。反过来会把
  rowmerge 用 `\n` 拼接的逻辑行边界并掉（p25 人员表 `党委书记\n董事长、董事` 实证）。
- 真实样本：茅台 2024 年报 p1–40 → 50 张表结构完全相同；多行单元格 250 → 38
  （212 处行内折行回流）；单元格总字数 14884 → 14585；`王 莉` / `王 鑫` /
  `韦 芳` / `闻 勇` / `蒋 焰` / `向 平` 等姓名伪空格消除。
- 原计划的 R-0041 在上述口径下本身无伪空格（p1–40 单元格 0 处），
  真正的收益是折行回流与行内填充空格；验收改用 p1–40 全量前后对照。
- `tests/e2e/test_deliverable_tables.py`：删除人名/单元格字面量快照，改为不变量断言
  （每逻辑行一个称谓、无重复、姓名无 CJK 空格、多值单元格每行非空、日期按行承载）；
  4 页参数化只是同一文档的回归锚点，**不是泛化**；对照口径改为
  `merge_logical_rows(normalize_table_grid(...))`。
- 泛化保证：新增 `test_cell_normalize_properties.py`（Hypothesis）——幂等、
  非空白字符不增不减、无残留 CJK 间空白、形状干净、纯 ASCII 多行不合并、
  网格结构不变、单元格互不污染；并做变异检验（去掉伪空格删除 → 性质测试
  以最小反例 `中 文` 失败）。

## 4. Phase 3：统一归一化入口（P0）

**问题**

- Markdown 回退路径：`normalize_cjk_text + repair_numbering`。
- 坐标建块路径（`_item_blocks`）：只 `normalize_cjk_text`，不做编号修复。
- 表格路径：两者都跳过。

**改动范围**

- `pipeline/quality/normalization.py`（建议新模块）：定义 `normalize_text` /
  `normalize_cell` / `normalize_table_grid` 三个入口，明确适用范围与幂等性。
- `pipeline/task/quality.py`：只调用统一入口。
- `pipeline/structure/build.py::_join_lines`：调用同一正文入口，不再各自组合。
- `tests/eval/prepare_review.py`：raw fallback 也走同一入口，避免查看器与交付不一致。

**实施要点**

- 明确三段流水：抽取文本 → 归一化文本 → 建块/渲染；禁止在渲染层再补规则。
- 归一化输入输出带 `warnings`（例如 `numbering_repaired`、`spaces_removed`）供观测。
- 保持幂等：`normalize(normalize(x)) == normalize(x)`，加属性测试。

**测试与验收**

- 三条路径用同一组 fixture（含 R-0023、R-0041、R-0036）产出相同文本；
- 幂等测试；编号修复不误改英文 `(2)`、不误改表格；
- 全量门禁。

**实施结果（2026-11-08）**

- 入口落在 `pipeline/quality/normalization.py`：`normalize_text` / `normalize_cell` /
  `normalize_table_grid`。机制仍在 `text_normalize`（整形）与 `numbering`（编号），
  本模块只定义“哪条路径用哪套口径”，避免各调用方自行组合。
- 统一口径：`build.py::_join_lines` 接入编号修复（此前坐标路径缺这一步）；
  `table/geometry.py`、`table/markdown.py` 的单元格改走 `normalize_cell`
  （不再套用正文硬换行规则）；`task/quality.py`、`tests/eval/prepare_review.py`
  改走 `normalize_text`。全仓已无绕过入口的直接组合。
- **幂等缺陷与修复**：单一顺序 `repair(normalize(x))` 不幂等——编号修复会制造新的
  CJK 相邻空白（`0()\t中` → `（0）\t中` → 再归一 → `（0）中`）。改为
  `normalize_text` 最多 4 轮收敛到不动点；性质测试（Hypothesis 200 例）通过。
- 观测 warnings 顺延到 Phase 6：`normalization_applied` 事件在那里定义，
  届时由调用方 before/after 对比得出 kind 与 changed，避免现在发明半成品 API。
- 验收：R-0023 真实页 `答：（1）`、无 `1 答：`、`造成21名`、`8人受伤`、
  标题 `## 198.` / `## 199.`、二次归一幂等；全量 1012 测试与门禁通过。

## 5. Phase 4：表格策略与缺口误判（P0/P1）

### 4.1 L1 表格指标改用路由/降级后的表（P0）

**问题**：R-0002 的页眉假表在 L1 阶段触发 `table_structure_broken`
（`row_col_variance=2.25 > 1.0`），整页变缺口；伪表降级发生在之后的建块阶段。

**改动**

- `pipeline/task/quality.py` 或 L1 前处理：对原始 Markdown 做轻量伪表判定
  （页眉表特征：贴页顶、首行时间戳、行数少、无框线），或直接使用路由/降级后的表格集合。
- `pipeline/structure/build.py::_is_prose_table` 的判据抽到共享策略模块，L1 与建块共用。
- 避免 L1 与建块“两次判表、两套输入”。

**验收**：R-0002 不再缺口；R-0033/R-0036 仍 0 表；R-0041 真实表保留。

### 4.2 密度阈值标定（P0）

**问题**：R-0077/0098/0102 因固定 `text_area_cm2=468` 与 2.0/1.0 阈值被判低密度；
扫描页应保持缺口。

**改动**

- 用黄金集标定 `CHAR_DENSITY_MIN_BY_TYPE` 与实际版心面积；按页型/分栏区分。
- 增加“稀疏但有效”规则：表格/表单/多栏页不按正文密度一刀切。
- 保留 `needs_ocr` 纯扫描缺口的明确标识。

**验收**：R-0077/0098/0102 不再缺口；R-0139/0140 仍为 `needs_ocr`；
黄金集 STP/缺口率对照不出现反向回退。

### 4.3 表格检测策略（P1）

- `pages_with_tables` 只作页级先验（实测 R-0033/R-0036/R-0002 均误报）；
- 有框线表优先 pdfplumber `find_tables(lines)`；
- 无框线表评估 pdfplumber `text` 策略，替代自建 L1 几何的主路径；
- 验收网关仍保留（`acceptance.py`），失败原因继续落日志；
- 目标：减少 `geometry.py` 自建启发式的使用面，而不是完全删除。

**验收**：真实表格样本（R-0041 等）结构不回退；假表样本保持 0 表；
路由日志可解释每次选择。

### 4.4 阈值策略化（P1）

- 新建 `pipeline/table/policy.py`（或 `TablePolicy` dataclass）集中：
  - 验收 `MIN_ROWS/COLS/FILLED_RATIO/TEXT_COVERAGE/ROW_RATIO`
  - 路由 `SELECTION_MARGIN/PAIR_MIN_OVERLAP/FRAGMENT_OVERLAP/L0_SUPPRESS_COVERAGE`
  - 伪表 `PROSE_CELL_MIN_CHARS/PROSE_TABLE_AREA_RATIO`
- 通过 `TaskConfig` / 配置注入，默认值保持当前行为；
- 来源字符串改 `StrEnum`（`markdown/geometry/ruled`）。

**验收**：默认值行为不变；单测覆盖策略注入；CHANGELOG 记录阈值来源。

**实施结果（4.1 / 4.2，2026-11-08）**

- 4.1 判据共用：新增 `pipeline/table/prose.py`（`SENTENCE_END` /
  `PROSE_CELL_MIN_CHARS` / `PROSE_CELL_SHORT_MIN_CHARS` / `PROSE_TABLE_AREA_RATIO` /
  `is_prose_cells`）；`build.py::_is_prose_table` 与
  `table_metrics.table_page_metrics(skip_prose=True)` 共用。`task/quality.py`
  的 L1 指标只统计真实表，`has_table` 由过滤结果决定，`has_columns` 取自
  `extraction.pages_with_columns`。
  与计划的差异：没有把 L1 改成“先跑表格路由再质检”（会把建块提前、放大文件打开次数），
  而是抽出同一判据、两侧各自轻量判断一次。
- 4.2 阈值：`CHAR_DENSITY_MIN_BY_TYPE` = 0.9 / 0.5 / 0.2 / 0.2，
  新增 `COLUMN_PAGE_DENSITY_MIN = 0.4`；`_density_threshold()` 同时服务门控与
  `extract_quality`，落地“按页型 + 表格/分栏的稀疏但有效”规则。
  仍沿用配置项 `text_area_cm2 = 468`，未改成逐页实际版心：
  L1 输入契约不含页尺寸，要加需修改 `PageArtifact` / 画像契约，留待后续。
- `PageGap.evidence` 增加 `needs_ocr` 标识（`repair/gap.py`）。
- 复核（逐页重放 L1）：R-0002 / R-0033 / R-0036 / R-0077 / R-0098 / R-0102 → pass；
  R-0139 / R-0140 → `encoding_loss` + `low_density`，`needs_ocr=True`；
  R-0162 → `low_density`（近空白，保持缺口）。
- 4.4 阈值集中：新增 `pipeline/table/policy.py` —— `TablePolicy` 收拢验收五项、
  路由四项、伪表四项（含 `prose_short_cell_count`），`TableSource` StrEnum 取代
  `"markdown"/"geometry"/"ruled"` 字面量。`TaskConfig.table_policy` 注入，
  交付与质检各自取用；默认值 = 迁移前行为（`test_policy.py` 锁定）。
- 4.3 无框线表评估：`PdfplumberTableSource(include_text_strategy=...)` 增加 text
  策略候选，与 lines 结果按 bbox 去重；路由器要求 text 候选必须有 L0 参考
  （`text_without_reference`），否则正文页会被 text 策略虚报成表。
- **评估结论（不启用）**：19 份文档 × 前 10 页（约 190 页，含围标/年报/法规），
  三种 text 变体（`text`、`text+tolerance`、`lines+text`）在验收网关下：
  候选 9 / 9 / 21，通过 6 / 7 / 12，**优于 L0 Markdown 0 次**。
  因此 `enable_text_strategy=False` 保持默认，geometry 仍是无框线表主路径；
  开关保留以便新语料复核。CHANGELOG 记录该依据。
- 明确未做：用 text 策略替换 geometry 主路径——缺乏样本证据；
  待补充无框线表格语料后重新评估。

## 6. Phase 5：结构拆分与去重（P1）

**问题**

- `pipeline/structure/build.py` 809 行、36 个顶层函数，混合行聚类、表格槽位、伪表降级、
  标题/段落、几何工具；违反 ≤500 行约束。
- `_area/_intersection_area/_overlap_ratio/_page_bbox/_union_bbox` 在 build/router/geometry/anchor
  重复；`_SEPARATOR_CELL` 重复 5 处；终止标点集合三套且不一致。

**拆分方案**

| 新模块 | 内容 |
| --- | --- |
| `structure/lines.py` | `_cluster_lines`、`_attach_inline_numbers`、`_gap_distance`、`_Line` |
| `structure/slots.py` | `_build_slots`、`_match_l0`、`_trusted_bbox`、`_is_prose_table` |
| `structure/blocks.py` | `_item_blocks`、`_markdown_blocks`、标题/段落/表格块 |
| `structure/geometry_utils.py` | `_page_bbox`、`_area`、`_intersection_area`、`_overlap_ratio`、`_union_bbox` |

- `table/markdown.py`、`quality/table_metrics.py`、`repair/*` 共用分隔行正则；
- `text_metrics.py`、`text_normalize.py`、`build.py` 共用终止标点集合（明确差异并文档化）。

**实施方式**

- 纯移动 + re-export，先不改行为；单测保持通过；import-linter 保持；
- 每个文件 ≤500 行；必要时把几何工具下沉到 `pipeline/table/geometry.py`。

## 7. Phase 6：观测与回归（P1）

**新增事件**

- `page_gap`：page、codes、metric、threshold、page_type、text_layer、source；
- `heading_decision`：page、text、source、level、reason；
- `normalization_applied`：page、kind（spaces/numbering/cell）、changed（bool）。

**回归与验收**

- 黄金样本固定断言：
  - R-0023：`答：（1）`、`造成21名…8人受伤`、无 `1 答：`
  - R-0033/R-0036：0 个 `<table>`，正文完整
  - R-0002/R-0077/R-0098/R-0102：不再是缺口
  - R-0139/R-0140：仍为 `needs_ocr`
  - R-0041：HTML 表格结构不变
- 重新生成 `review_rescore` / `review_final` / `review_v2`；
  `artifact_hash` 变化后旧分自动失效，`calibrate.py` 应返回 `stale_labels`；
- 人工重评后再跑 `calibrate.py` / `qc_gate_report.py`。

## 8. Phase 7：残留质量（P2）

- R-0023 段落切分偏碎（`1-59`/`案例`/`2021` 各自成段）：段落合并策略调优；
- R-0033 页眉/页脚仍与正文合并：页眉页脚识别与剔除；
- 扫描页在查看器/报告中使用独立标签（`needs_ocr`），不计入识别失败；
- 表格跨页/合并单元格的进一步回归样本。

## 9. 总体验收门禁

```bash
uv run pytest -q                       # 全量测试
uv run ruff check src tests scripts
uv run mypy src
TERM=dumb uv run lint-imports
bash scripts/check-gates.sh
```

补充：

- `src` 下无 >500 行文件（Phase 5 后恢复）；
- 黄金样本断言全部通过；
- CHANGELOG 记录权重/阈值/归一化口径变化；
- 每个 Phase 单独 commit；Phase 1–4 完成后重新生成标注查看器并复核。

## 10. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 标题层级变化导致结构树/交付 diff 大 | 先加回归断言，再改规则；按 Phase 提交 |
| 表格单元格归一化改变 HTML 文本 | 只改文本不改 grid；R-0041 对账 |
| 密度阈值调整导致缺口率反向 | 黄金集前后对照；保留扫描页缺口 |
| 伪表判定误降真实表 | 表格覆盖页 + 散文单元格双条件；真实表样本回归 |
| build.py 拆分引入行为变化 | 纯移动 + re-export，单测与 import-linter 通过 |
| 旧人工分数失效 | artifact_hash 绑定；calibrate 拒绝 stale；重评后重拟合 |

**回滚**：每个 Phase 独立提交，出现问题直接 revert 该 Phase；不混合行为改动与结构移动。
