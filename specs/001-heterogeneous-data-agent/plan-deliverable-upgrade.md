# 交付产物升级实施方案 v2（定稿 2026-11-04）

|                |                                                                                              |
| -------------- | -------------------------------------------------------------------------------------------- |
| **Date**       | 2026-11-04（v2，取代 v1；v1 中"每文档一目录""占位行/管道表内嵌"等结论已被本轮讨论作废）          |
| **依据契约**   | `specs/001-heterogeneous-data-agent/contracts/deliverable-format.md`（第三版定稿）              |
| **目标**       | 把已建成的表格择优能力接入主链路，按"结构树真身 + 双视图交付"重建交付层，并覆盖规模化交付形态    |

## 0. v1 → v2 的关键修正（讨论结论收编）

| # | 修正项 | v1 结论 | v2 定稿 | 理由 |
| - | ------ | ------- | ------- | ---- |
| 1 | 表格在 md 中的形态 | 占位行 / 展开式管道表 | **HTML 内嵌 + `<!--table:tNNN-->` 锚点 + 可见表题行** | 人眼核验合并结构；对齐 MinerU/PaddleOCR 生态；锚点让切块器原子化抽表 |
| 2 | 切块输入 | 基于 JSON 结构树切 | **基于 Markdown 文本切**（切块器内部重建标题树） | LlamaIndex/LangChain 主流切块器直接可用；JSON 服务结构化链，两链各用各的 |
| 3 | 目录结构 | 每文档一个目录（md+json 同目录） | **`md/` + `json/` 两个大文件夹 + 一层哈希分桶** | 目录元数据开销最小；对象存储上"文件夹"只是 key 字符，零成本 |
| 4 | JSON 内容范围 | 未明确 | **全文结构树**（正文+标题层级+全部表格 grid/rows_text/bbox） | 层级关系只有结构树保得住；表格按 `type/table_id` 过滤三行代码取出 |
| 5 | 表格单独产物 | 无 | **可选第三产物 `<hash>.tables.json`**（任务级开关） | "只要表格"的消费方（物化入库、符合性比对）不用读全量 JSON |
| 6 | 建树方式 | 阶段一先替换 md 文本，阶段二再把 md **解析回**结构树 | **解析时直接建树**（行扫描带坐标 + 择优结果按槽位入座），md/json 均由树渲染 | 杜绝"渲染后再解析"的倒手；树只建一次，视图打印两次 |
| 7 | 表格去重机制 | 未明确 | **"一张表先吃掉 bbox 内一切文字行，再占位"** | L0 管道表行被识别为槽位换内容；L0 漏识别的表吞并范围内文字行后造槽位，双表冲突结构性不存在 |
| 8 | 输出位置/访问方式 | 未明确 | **散文件是唯一交付形态，任何规模不打包**；输出根目录复用 `output_dir` 注入点；**默认 = 服务器磁盘 + 前端 HTTP 下载**；NAS 走 OS 挂载（零代码，优先）或 WebDAV-SMB 后端插件（按需）；自然语言可路由交付位置，不可达/无凭证回退默认 | 私有化部署无传输环节，打包纯属多余操作（2026-11-05 评估后删除 tar） |

## 1. 现状核验（代码级，v2 重新确认）

| 能力 | 状态 | 证据 |
| ---- | ---- | ---- |
| pdfplumber 有框线提取 | ✅ 已建，**主链路零调用** | `platform/plugins/adapters/table_pdfplumber.py` 仅被 `scripts/validate_tables.py` import |
| 三路择优路由（L0/L1/L2 + 验收网关） | ✅ 已建，**主链路零调用** | `pipeline/table/router.py::route_tables` 调用方只有验证脚本与单测 |
| TableGrid → Markdown 渲染 | ✅ 已建，仅测试/查看器用 | `pipeline/table/render.py::to_markdown` |
| TableGrid → HTML 渲染 | ❌ 未建 | 需新增 `pipeline/table/html.py` |
| 交付 Markdown | ❌ 仍是 pdf-inspector 原生 L0 | `pipeline/output/formats.py::to_markdown` 直接 `return document.markdown` |
| JSON 结构树交付物 | ❌ 未建（现有 to_json 是页级 markdown 套壳） | `pipeline/output/formats.py::to_json` |
| 结构树标题层级 / 块 ID / 表 ID / rows_text | ❌ 未建 | `contracts/structure.py` 无 level / block_id / table_id / rows_text |
| 交付多格式通道 | ✅ 已建（config 驱动） | `task/delivery.py::write_outputs` 按 `config.output_formats` 写 `<output_dir>/<doc_id>.<后缀>`，落盘改造只需动这一处 |
| 任务级输出位置 | ✅ 已建 | `DocumentDeliveryService(output_dir=...)` 任务级注入——**方案无需新增 `deliver_to` 字段**，复用现有注入点 |
| warnings 通道 | ✅ 已建 | `PageArtifact.warnings: tuple[str, ...]`（表格定位失败回退 L0 直接挂这里） |
| 锚点注释 / bbox 吞并 / tables.json / 分桶布局 | ❌ 未建 | 全库无对应实现 |

**结论**：解析与择优能力已就绪且经过离线验证（p24/p25/p60/p86 四页实测、构造样本 cell F1 = 0.9917），主链路交付还是 L0 原样透传。缺的是三块：**接入 + 抽取侧增量（坐标项/结构元素）+ 交付层重建**。

## 2. 目标架构总览

### 2.1 单页数据流（页级并行时每页独立，页码为排序键；当前任务路径仍为单进程顺序）

```
pdf-inspector 抽取 ──→ 页 markdown 文本（L0）                     ~16ms/页
坐标项提取（仅含表格页，门控）──→ PositionedText 列表（带 mcid）  ~10ms/页（仅表页）
结构元素提取（全页，成本极低）──→ (page, mcid, role: H1–H5/P/…)  ~2ms/页
table_pdfplumber ──→ ruled_tables（仅含表格页跑）                2–5ms/页
        │  坐标项/结构元素经 pipeline 内部侧通道传递：
        │  DocumentContext 页级旁车（doc_id+page_no 键），PageArtifact 插件契约冻结不动
        ▼
route_tables 择优 ─→ 每张物理表一个最优 TableGrid + 来源          <1ms
        │
        ▼
【建树 · 双路建块】（核心机制）
  tagged PDF：结构元素定块边界与角色（标题 level 直接来自 H1–H5）
  untagged 回退：坐标项 + 行聚类启发式
  连续 "|" 段 / 坐标聚类表区 → 表格槽位（默认装 L0 grid）
  route 选中的 grid → 按锚点入座槽位；
  无槽位的表（L0 漏识别）→ 过安全阀后吞并 bbox 范围内文字行、插入槽位：
    安全阀 = 投影成功拿到真实 bbox 且面积占比 < 阈值（默认 60% 页面积）
    不满足（典型：L0 投影失败退化为全页 bbox）→ 只插槽位不吞并 + warnings
  规则统一：一张表先吃掉自己 bbox 内的一切文字行，再占位 —— 同位置永无双表
        │
        ▼
DocumentStructure（页 → 块序列：heading/text/table，块带 block_id/bbox/level）
        │
        ├── 打印 md 视图：# 标题 + 正文 + 表题行 + <!--table:tNNN--> + <table>（现场渲染，微秒级）
        ├── 打印 json 视图：整树序列化（含 rows_text 预生成）
        └── 可选打印 tables.json 视图：仅表格块
```

**铁律**：树只建一次；两个/三个视图是同一份内存对象的多次序列化，**永不回读已渲染文本再解析**。

### 2.2 并行与跨页

- 页是天然并行单元：每个线程拿"一页输入"产出"一页块列表"，互不依赖；
- 汇总只按 page_no 排序拼接，与线程完成先后无关；
- **顺序约束（PR-1 起生效，防功能回退）**：现行 `_merge_cross_page_tables` 基于 L0
  管道表文本，必须在建树/表格替换**之前**、于原始页 markdown 上执行；建树在合并后
  的页上进行。网格级结构合并（PR-3）届时原位替换文本版，顺序不变；
- 跨页表合并（文本版）是**后处理 pass**：对按序页序列中"相邻页管道表"做行追加+表头
  沿用判断，不引入提取阶段的全局依赖。

### 2.3 保留 L0 / L1 的依据（回答"为什么不只留 L2"）

| 路 | 分工 | 不可替代性 |
| -- | ---- | --------- |
| L2（pdfplumber） | **有框线表**最优：按真实线读边界，合并单元格可推 span | 对**无框线表（三线表、无线表）完全无能为力**——财报/论文里大量存在 |
| L1（坐标聚类） | **无框线表**主力：不依赖线，按文字坐标聚类行列 | 无框线场景的唯一网格候选；同时产出 `table_cell_fill`/`table_header_ok` 门禁指标 |
| L0（管道表） | 兜底：L1 聚类失败、简单表不值得跑聚类时 | 抽取正文时**零成本顺带产出**；删除它无收益 |

实测印证：p24 瀑布图旁小表因竖线被图形截断，pdfplumber 重建失败而 L0/L1 成立——只留 L2 这张表就是零。

## 3. 阶段一：表格择优接入 + 页块构建（最小闭环）

**目标**：交付物中表格 = 验收网关选出的最优 TableGrid，按契约第三版格式（表题行 + 锚点 + HTML）渲染。展示界面立即改观。

**开工前先做原型验证（2026-11-05 代码核验后新增，半天内）**：用茅台年报（143 页）
实测双路建块可行性——`extract_structure_elements` 在该文档上是否有输出
（tagged/untagged 判定）、(page, mcid) join 覆盖率、untagged 回退路的块边界质量。
原型结论决定双路各自投入，原型代码不进主干。

1. **解析段增量提取（坐标项 + 结构元素）**：
   - 坐标项：`extract_text_with_positions`，**按 `pages_with_tables` 门控**——仅含表格
     页提取（无表页零增量，压住实测 +30% 的抽取增幅）；
   - 结构元素：`extract_structure_elements`，成本极低（实测 143 页 0.22s），全页提取；
   - **页码基准按接口分别固定**（实测）：`extract_pages_markdown` 的 `pages=` 与返回
     `page` 为 0-indexed（经 `contracts/pages.py` 转换）；`extract_text_with_positions` /
     `extract_structure_elements` 的 `pages=` 与返回 `page` 均为 1-indexed，直接使用
     内部页码，**不得**再套 0-indexed 转换；
   - **落层**：新增 pdf-inspector 调用统一放 `platform/plugins/adapters/`（如
     `layout_probe.py`），pipeline 只消费 `PositionedText` / 结构元素 DTO，不直接
     import pdf-inspector（沿用 `geometry.py` 的既有边界）；
   - `PositionedText` 增 `mcid` 字段（geometry.py 的 `from_pdf_inspector` 目前丢弃了
     pdf-inspector TextItem 自带的 mcid，需补上）；
   - **承载通道 = pipeline 内部侧通道**：`DocumentContext` 增页级 layout 旁车
     （doc_id + page_no 键），抽取段写入、建树段读取；`PageArtifact` 插件契约冻结不动；
   - **生命周期**：建树完成即释放本页坐标项/结构元素，仅保留块列表（774 页约 7 万
     坐标项，不能随任务收尾前的上下文累积）。
2. **跨页合并顺序对调**：现行 `_merge_cross_page_tables` 基于 L0 管道表文本识别
   （merger.py `_table_blocks`），必须先于建树/表格替换、在原始页 markdown 上执行；
   建树在合并后的页上进行。否则 PR-1 会把跨页合并静默置失效（功能回退）。
3. **新增建树 pass**（建议 `pipeline/structure/build.py`）：
   - 输入：合并后的页 markdown、侧通道坐标项 + 结构元素、该页 ruled_tables
     （`route_tables` 现有签名 `(markdown, items, *, page_no, ruled_tables)` 直接匹配，
     无需改接口）；
   - 调 `route_tables()` 择优；
   - **双路建块**：tagged PDF 用结构元素（角色直给标题 level 与块边界，不回读
     markdown 反推行级 bbox）；untagged 用坐标项 + 行聚类启发式；连续 "|" 段 /
     坐标聚类表区 → 表格槽位；
   - **bbox 吞并安全阀**：仅当表投影成功（拿到真实 bbox）且 bbox 面积占比 < 阈值
     （默认 60% 页面积）才吞并范围内文字行；不满足（典型：L0 投影失败退化为
     全页 bbox，markdown.py 默认 bbox + router 回退路径）→ 只插槽位不吞并 +
     warnings。**"中心点落入"规则防不了全页 bbox，必须配面积阈值**；
   - **无坐标源回退**（CSV/text 家族、`text_layer_recovered` 页、纯扫描页）：按
     markdown 行建块，bbox 取页框并标记来源（如 `bbox_source=page`）；表格槽位由
     连续 "|" 段识别，不吞并（无坐标可吞），契约的 bbox 字段仍必填；
   - 产出页块列表；定位失败保留 L0 并计数，warnings 经 `PageArtifact.warnings`
     既有通道透出（该字段已存在，无需新建机制）。
4. 新增 `pipeline/table/html.py`：`TableGrid → HTML`（rowspan/colspan 无损）+ 锚点注释
   + 表题行渲染（含 caption 启发式：表格上方最近短行匹配 `表\s*\d+[-—.]?\d*` 编号
   模式，无匹配回退 ID-only；被提升为 caption 的行从正文块中移除），纯函数、幂等。
5. 阶段一交付仍走现有 delivery 通道，仅把 md 产物来源从 `document.markdown` 换成"块列表渲染结果"。

**验收**：原型验证报告先行；e2e 四页真实样本（p24/p25/p60/p86）交付物表格与
`validate_tables.py` 选中输出一致；**双表对账测试**（全文检索任意表格 ID 只出现一次、
正文流中不存在被吞并区域内残留文字行）；**吞并安全阀测试**（构造投影失败的全页 bbox
用例，断言不吞并且出 warning）；跨页合并样本新旧行为一致；60 页黄金集回归无回退。

## 4. 阶段二：结构树真身 + 双视图交付

**目标**：`DocumentStructure` 成为唯一真身，md/json 同源双视图落地。

1. `contracts/structure.py` 扩充：
   - `TextBlock` 增 `level: int | None`（标题层级）与 `block_id`（`b` + 十进制序号；
     同一文档内定宽，至少四位，超过 9999 块自动加宽）；
   - `TableBlock` 增 `table_id`（`t` + 十进制序号，定宽规则同 `block_id`：默认三位，
     超过 999 表自动加宽）、`caption`、`rows_text`；
   - ID 按文档内阅读顺序分配，md/json 严格一致。
2. `_normalize_headings`（标题层级平滑）从 `merger.py` 迁为结构树操作，`merger.py` 退化为纯页序/缺口组装。
3. `pipeline/output/render.py` 两个出口：
   - `to_markdown_view(structure)`：level → `#`、正文原文、HTML 表格 + 锚点 + 表题行；
   - `to_json_view(structure)`：整树序列化（契约 §2.2）。
4. rows_text 生成器：从 TableGrid 展开（合并单元格按值重复/"其中："前缀），幂等纯函数。
5. `pipeline/task/delivery.py`：交付清单增加 `.json` 产物。
   - **依赖扫描已完成（2026-11-04）**：`formats.py` 的调用方仅 `delivery.py` 与
     `output/__init__.py` 再导出，无其他下游——旧 `to_json`（页级套壳）下线风险低，
     替换为结构树 JSON 视图即可。
   - **输出位置**：`DocumentDeliveryService(output_dir=...)` 本身就是任务级注入，
     **无需新增 TaskSpec 字段**——`deliver_to` 复用现有 `output_dir` 注入点
     （任务指定走任务参数，缺省走全局默认）；经存储抽象落盘。
6. **md/json 自动对账测试**：md 中每个 `tNNN` 在 json 中存在且 grid 一致；rows_text 行数与网格逻辑行对账。
7. **流式交付（中断可续，用户明确要求"每份解析合格即落盘"）**：每文档 finalize 合格
   即写盘该文档 md/json——交付写盘从"任务收尾统一写"（现行 `write_outputs` 由
   task_runner 收尾统一调用）前移到单文档完成处；任务收尾只补 manifest 与统计。
   中断恢复 = 跳过已落盘文档，不重跑合并交付阶段。

**验收**：对账测试通过；黄金集 60 页标题层级与现行 `_normalize_headings` 输出一致；同一结构树渲染两视图，表格 ID、标题路径、行数全部一致。

> **PR-2 落地注记（2026-11-05）**：契约字段、结构视图（`pipeline/output/structure_view.py`）、
> `{meta, structure}` JSON、`rows_text` 与 md/json 对账已落地。PR-2b 已完成：
> `_normalize_headings` 迁入结构树、每文档 finalize 即写盘并释放重负载上下文。

## 5. 阶段三：规模化细节与消费侧产物

1. **跨页表合并改造**：`_merge_cross_page_tables` 从管道表文本行改为基于 TableGrid（行追加 + 表头沿用），触发条件沿用（上页触底 + 本页页首 + 列数一致 + 表头相似 ≥0.8）；`contracts/table.py::TableGrid` 增 `page_span: tuple[int, int]`（单页表为 `(page_no, page_no)`），合并块 bbox 取起始页表 bbox；黄金集跨页样本先跑对照。
2. **L1 指标口径切换（PR-3a 先行落地）**：`table_cell_fill` 低于 0.90 不再单独判坏——
   仅当行列占用率也低（< 0.8）才判 `table_structure_broken`；表页字符密度阈值单独用
   0.5（P0 初值，B9 固化）。60 页双口径对照：旧口径 41 页失败 → 新口径 24 页，
   17 页缺口转通过、0 页反向变差。后续再把指标来源切到最终 TableGrid。
3. **可选第三产物 `<hash>.tables.json`**：仅表格块（grid + rows_text + ID + 页码 + bbox），任务级开关（默认关）；供"只要表格"的结构化消费方。
4. **落盘布局**：`<输出根>/md/<hash 前 2 位>/<hash>.md` + `<输出根>/json/<hash 前 2 位>/<hash>.json`（+ 可选 `tables/` 同构）。两文件夹 + 一层分桶，不建每文档目录；对象存储下 key 即路径。

**验收**：跨页样本合并结果与旧口径对照无回归；tables.json 开关开/关产物一致性测试；分桶下单目录条目数 ≤ 数百。

## 6. 阶段四：交付访问层与规模验证（可后置）

> 2026-11-05 评估后重写：**私有化部署无传输环节，打包（tar/分片/单包）从交付层全部删除**。
> v1/v2 的 tar 方案是把"传输场景"的需求误套在交付层，作废。

### 6.1 交付形态唯一：散文件，无打包

任何规模（含 10 万份级）交付物都是散文件落"输出根目录"，不压缩、不打包；大批量下
文件定位经 manifest 或库中产物 key，不依赖目录列举。对象存储/磁盘均不存在小文件问题。

### 6.2 默认链路

**服务器磁盘（默认输出根目录）→ 用户经前端 HTTP 下载**。用户不指定交付位置时，
这是唯一默认行为。

### 6.3 展示层下载能力（apps，四项）

| 能力 | 机制 |
| ---- | ---- |
| 单文件下载 | 直接 HTTP 下发，浏览器存默认下载目录 |
| 多选批量下载 | 前端并发发起多个单文件下载（浏览器关闭"逐文件询问保存位置"即全自动入下载目录） |
| 打包下载 | 服务端**按需流式 zip**：点击时边读散文件边压缩边传，不落盘、不占存储——是下载动作，**不是交付格式** |
| manifest 下载 | 交付清单下载，供程序批量消费 |

### 6.4 manifest：可选索引文件

交付完成后在输出根目录生成：一行一文档（`doc_id / md、json 产物 key / 交付等级 / 页数 / 缺口数`）。
供程序批量遍历与下载对账；非包、非必经，任务级开关。

### 6.5 交付目标扩展（可选，插件形态）

- **存储后端扩展点**：本地盘 ✅ / S3 兼容（MinIO）✅ / WebDAV-SMB 后端插件 ⬜（按需再做）；
  NAS 优先走 **OS 挂载 + output_dir 指挂载点**（零代码，写入部署文档，先于插件实现）；
- **NL→Plan 路由**：用户自然语言指定交付位置（"存到公司 NAS 财报库"）→ 解析为
  deliver target → 命中已配置且可达的后端则路由；不可达/无凭证 → 回退默认
  （服务器磁盘 + HTTP 下载）并明确告知原因；不指定 → 默认；
- **凭证预置为前提**：首次接入是配置动作（登记存储地址 + 凭证），之后自然语言才可路由；
  NL 不能越过网络可达性（服务器到不了的地址只能回退）。

### 6.6 规模验证

大批量压测对齐 PRD（16 核 / 1000 页/秒，即 16ms/页/核；实测茅台年报 markdown
抽取 33ms/页，压测前需复核基准文档与并行度）；流式落盘（阶段二第 7 项）下的中断恢复演练：
杀掉任务 → 重启 → 已落盘文档跳过、未完成的续跑。

**验收**：下载四项功能测试；NL 路由命中与回退测试；manifest 与落盘产物对账；中断恢复演练通过。

## 7. 明确不动的边界

- 解析与择优链路本身（`route_tables`、验收网关、阈值）不改逻辑，只接调用；
- 状态机、调度器、插件契约、存储抽象不动；
- L2 采样、修复回路、RulePack 不受影响；
- 消费侧（切块/向量化/入库/混合检索）不在本方案范围，契约 §3 只是交付约定与使用指引。

## 8. 风险与缓解

| 风险 | 缓解 |
| ---- | ---- |
| bbox 吞并误伤正文（投影失败退化为全页 bbox 吃掉整页） | **安全阀**：仅投影成功且 bbox 面积占比 <60% 才吞并，否则只插槽位 + warning；构造用例进单测 |
| 结构元素仅 tagged PDF 可用，财报大量 untagged | 双路设计：untagged 走坐标启发式回退；原型先行测覆盖率再定投入 |
| 表格槽位定位失败率高于预期 | 失败保留 L0 + warnings 计数，纳入 L1 观测，不静默 |
| 跨页合并改结构版引入回归 | 沿用既有触发口径；跨页黄金样本先跑新旧对照 |
| L1 指标口径切换导致阈值失真 | 双口径并行一轮，比对后再切基线 |
| rows_text 展开策略在非标财报上失真 | 展开规则写死渲染器；真实财报样本验收 |
| tables.json 第三产物增加维护面 | 任务级开关默认关；与 json 内表格块同源生成（同一次渲染 pass） |
| 页级旁车在任务收尾前累积（774 页约 7 万坐标项） | 建树完成即释放坐标项/结构元素，仅保留块列表；PR-2 流式落盘后上下文随文档释放 |
| 无坐标源页面（CSV/text/recovered/pure scan）无法吞并 | 按 markdown 行建块、bbox 标记页框来源；只插槽位不吞并 |

## 9. 执行顺序与 PR 切分

1. **PR-1（阶段一）**：原型验证（结构元素双路建块）→ 坐标项/结构元素侧通道（含
   页码基准与落层约定）→ 跨页合并顺序对调 → 建树 pass（双路建块 + 吞并安全阀 +
   无坐标源回退）→ HTML 渲染（含 caption 去重）+ 双表对账测试 → 展示界面可见正确效果；
2. **PR-2（阶段二）**：contracts 扩充 + 双视图渲染 + delivery 改造 + md/json 对账测试；
3. **PR-3（阶段三）**：跨页合并结构版 + L1 口径切换 + tables.json 开关 + 分桶布局；
   - PR-3a（质量口径修复）已先于完整 PR-3 落地：L1 表格结构判定引入行列占用率与
     表页密度阈值 0.5；完整 PR-3 剩余跨页网格合并、tables.json、分桶；
4. **PR-4（阶段四）**：交付访问层（下载四项/流式 zip 导出/manifest）+ 交付目标扩展点
   （挂载指引、WebDAV-SMB 插件按需、NL 路由）+ 规模验证（业务量触发）。

TDD：每个 PR 先写验收测试再实现（沿用项目门禁与 ADR-008 契约纪律）。
