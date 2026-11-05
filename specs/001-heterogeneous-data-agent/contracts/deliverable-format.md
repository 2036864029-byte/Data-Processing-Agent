# 交付产物格式契约（定稿 2026-11-04；2026-11-05 修订）

> 2026-11-05 修订：依 PR-1 前代码核验，明确 csv/excel 地位、JSON 双层结构、
> 缺口页表示、表题 caption 提取规则；删除 tar 打包交付形态（见 §4 修订注记）。

> 回答：每份文档解析后交付什么、表格以什么形态存在、下游（切块/向量化/入库）如何二次处理。
> 本契约是实现 `pipeline/output/` 渲染层与 `contracts/structure.py` 扩充的依据。

## 1. 核心原则

1. **解析真身是文档结构树**（`DocumentStructure`），不是 Markdown。
   Markdown 与 JSON 是结构树的**两个同源渲染视图**，由同一份结构生成，禁止互相转换。
2. **机器消费结构，人读渲染视图**（延续 `contracts/structure.py` 既有定位）。
   JSON 是无损真源（表格网格、bbox、层级都在）；切块输入用 Markdown 视图
   （主流切块器生态成熟），两者同源等价，分工见第 3 节。
3. **Markdown 中表格以 HTML 内嵌**（对齐 MinerU / PaddleOCR 生态约定，人看还原度、
   机读有锚点），每个表格上方带机器锚点注释 `<!--table:tNNN-->` 与可见表题行。
   表格向量化消费一律经 JSON 中的 `rows_text`，不让 HTML 标签流进入 embedding，
   也不让 LLM 直接读 HTML 原文——切块器负责完成这个转换。

## 2. 交付物

每份文档的**标准交付物**是两个文件（文件名同前缀，内容哈希主键不变）：

| 文件 | 内容 | 消费者 |
| ---- | ---- | ---- |
| `<doc_hash>.md` | 标题层级（`#`）、正文、全部表格（HTML 内嵌 rowspan/colspan 无损 + 锚点注释 + 表题行） | 人读、核验、LLM 通读（经切块器转换）、渲染展示 |
| `<doc_hash>.json` | 完整结构树：块序列、标题层级、表格网格（含 rowspan/colspan 无损还原）、bbox、表 ID、页码、rows_text | 程序、表格取值、渲染重建 |

> csv / excel 为**既有可选派生视图**（`TaskConfig.output_formats` 既有能力，有单测
> 与用户在使用），由同一份合并/结构结果生成（阶段二后真身为结构树），保留不动；
> 它们不是结构树真源，本契约不约束其格式。表格专用的可选第三产物
> `<hash>.tables.json` 见 §4 与实施方案阶段三。

### 2.1 Markdown 视图规则

- 标题层级用 `#` 系标题表达，与结构树中 heading 块的 level 一一对应。
- **所有表格以 HTML 内嵌正文流**（MinerU / PaddleOCR 生态同款形态），三层标记：

  ````markdown
  表 3-1 合并资产负债表（t003）
  <!--table:t003-->
  <table>
  <tr><th rowspan="2">项目</th><th colspan="2">期末余额</th>...</tr>
  ...
  </table>
  ````

  1. **可见表题行**：人工核验时一眼对上（caption 缺失时仅输出 ID 行）；
  2. **`<!--table:tNNN-->` 锚点注释**：机器锚点，切块器一行正则即可定位，
     其下 `<table>...</table>` 整体视为原子块，不在 `<tr>` 中间下刀；
  3. **HTML 表格**：无损形态（rowspan/colspan 与原 PDF 一致），核验时可见
     合并结构还原是否正确，同时供下游渲染展示。

- 表序号按文档内阅读顺序编号；表 ID 与 JSON 中表格块严格一致。
- **表标题（caption）提取规则**：取表格上方最近的短行、匹配 `表\s*\d+[-—.]?\d*`
  等编号模式作为 caption；无匹配时仅输出 ID 行。caption 同时写入 JSON 表格块。
  被提升为 caption 的行从正文块中移除（提升即消费，避免表题在正文中重复出现）。

### 2.2 JSON 视图规则

顶层结构为 `{meta, structure}` 两层：

- **meta**：doc_id、delivery_level、gap_ratio、requires_manual_review、validation
  （现行 `to_json` 的分级交付元数据全部保留，供分级交付与质检消费，不丢失）；
- **structure**：结构树，唯一无损真源，字段要求：
  - 块按阅读顺序排列；每个块必带 `page_no`、`bbox`、稳定 `block_id`
    （`b` + 十进制序号；同一文档内定宽，至少四位，超过 9999 块自动加宽）。
  - 标题块必带 `level`，层级关系由块序列与 level 表达（同 Markdown 标题语义）。
  - 表格块持有 `TableGrid`（行列、span 无损），必带文档内唯一表 ID
    （`t` + 十进制序号，定宽规则同 `block_id`：默认三位如 `t003`，超过 999 表自动加宽）。
  - 每个表格块必携带**预生成行序列化文本** `rows_text`：表格每行渲染为一行文本，
    行内每列写 `列名: 值`，行间换行；
    复杂表按行展开时保留"其中："等归属前缀。由渲染器从网格生成，消费方切块时零成本取用，
    无需自行理解网格结构。
  - 跨页表合并后作为一个表格块出现，`page_no` 记起始页，页跨度记于
    `TableGrid.page_span`（`(起始页, 结束页)`；单页表为 `(page_no, page_no)`）；
    合并块 `bbox` 取起始页该表的 bbox（跨页无统一坐标空间）。

**缺口页表示**：`meta.gaps` 数组（页码 + 缺陷码），与上述 `{meta, structure}` 两层
结构一致；缺口页不出现在 `structure.pages` 中（与现行页序校验一致）；
md 视图维持注释占位不变。

### 2.3 表 ID 对应关系

Markdown 表题行中的表 ID 与 JSON 中表格块的 ID 严格一致，是跨视图关联的唯一手段。
消费方按 ID 从 JSON 取精确值，不让 LLM 直接读 JSON 原文。

## 3. 下游二次处理（消费侧，不在本流水线内）

切块输入是 **Markdown 文本**（LlamaIndex MarkdownNodeParser / LangChain
MarkdownHeaderTextSplitter 等成熟切块器直接可用），表格不进入正文切分流。
推荐流程（与 RAGFlow 表格独立块、LlamaIndex"摘要做检索原文做答案"同构的规则版）：

1. **普通内容**：按标题层级 + token 数切块，每块带标题路径元数据。
2. **表格**：由锚点注释 `<!--table:tNNN-->` 定位，其下 `<table>...</table>`
   整体抽为**原子块**，不切散任何一行。
3. **表格 chunk 内容**：向量化/喂 LLM 的文本 = 表题 + 按表 ID 从 JSON 取的
   `rows_text`（无 HTML 噪音）；HTML 原样保留在 chunk 内供渲染回填。
   超长表按行组切（如每 20 行），每组重复携带表题行与表头——行组 chunk 比整表
   chunk 向量稀释小，检索粒度更准。
4. **向量化入库**：正文 chunk 与表格 chunk 分别 embedding，元数据 =
   `{文档 ID, 表 ID, 表题, 页码, bbox}`。数字/科目名类查询建议混合检索
   （向量 + BM25），关键词通道对表格召回更稳。
5. **检索消费**：命中表格 chunk，对应行组（必要时整表 rows_text）喂 LLM；
   精确取值（如财报金额、招投标评分）按表 ID 回 JSON 读取，不经 LLM 抄录；
   需要给用户展示表格时，chunk 内 HTML 直接渲染。
6. **结构化二次处理**（财报物化入库、招投标符合性比对等）：直接消费 JSON
   结构树 / TableGrid，不经 Markdown。

**核验**（本流水线负责提供依据）：
- 机器：黄金集 cell F1、L1 指标（table_cell_fill / table_header_ok）、
  验收网关统计；比对真身是 TableGrid，与 Markdown 渲染无关。
- 人工：Markdown 内嵌 HTML 表格可直接阅读/渲染核验（合并结构可见）；
  需对照原图时用 `scripts/validate_tables.py` 四栏对照。

**禁用**：朴素字符切分器直接切 Markdown（会切断表格）；把 HTML 标签流直接
embedding；让 LLM 直接读 JSON 原文。

## 4. 输出位置与访问方式

- **每份文档的标准交付物是 2 个文件**（+ 可选第三产物，见 §2；启用 `output_formats`
  时另生成 csv/excel 派生视图，见 §2 注记）：JSON 是**全文结构树**
  （正文块、标题层级、全部表格的 grid/rows_text/bbox 都在其中），表格不单独成文件。
- **输出根目录**：任务级可指定（复用现有 `output_dir` 注入点），不指定用全局配置默认
  （服务器磁盘）；经存储抽象落盘（本地盘 / S3 兼容同构，WebDAV-SMB 为可选后端插件）。
- **目录结构**：`<输出根>/md/<hash 前 2 位>/<doc_hash>.md`、
  `<输出根>/json/<hash 前 2 位>/<doc_hash>.json`（可选 `tables/` 同构）。
  内容哈希命名 + 一层分桶，不用语义文件名（重名/改名/非法字符交给哈希解决；
  文档标题在 JSON 内，机器按哈希索引）。
- **流式交付**：每文档 finalize 合格即落盘自己的 md/json，任务收尾只补 manifest
  与统计；中断恢复 = 跳过已落盘文档。
- **任何规模均不打包**：散文件是唯一交付形态，大批量同样如此（私有化部署无传输
  环节；对象存储/磁盘无小文件问题）。定位产物经 manifest 或库中产物 key，
  不依赖目录列举。
- **默认访问方式 = HTTP 下载**（展示层能力）：单文件下载、多选批量下载、
  按需流式 zip 导出（点击时生成、不落盘、**非交付格式**）、manifest 下载。
- **可选交付目标**：NAS/SMB 优先走 OS 挂载 + 输出根目录指挂载点（零代码）；
  协议直连后端（WebDAV/SMB 插件）按需再做；自然语言可路由交付位置
  （NL→Plan 解析 deliver target），不可达/无凭证回退默认（服务器磁盘 + HTTP 下载）
  并告知原因。
- **manifest**：交付完成后生成的可选索引文件（`doc_id → 产物 key、交付等级、页数、
  缺口数`），供程序批量遍历与对账；任务级开关。

> 2026-11-05 修订：删除 tar 打包交付形态。此前"大批量 tar+manifest"是把传输场景
> 的需求误套在交付层；私有化部署默认服务器磁盘 + HTTP 下载，挂载/插件为可选扩展。

## 5. 实现落点

- `contracts/structure.py`：TextBlock 补 `level` 与 `block_id`；TableBlock 补
  `table_id` / `caption` / `rows_text`；DocumentStructure 增加块 ID 与表 ID 规则。
- `contracts/table.py`：`TableGrid` 补 `page_span`（跨页合并块，随阶段三落地）。
- `pipeline/output/`：新增结构树渲染器（structure → md / structure → json 两个出口），
  替换现行"产物 Markdown 直接落盘"的单视图输出。
- 表格渲染策略（HTML 内嵌 + 锚点注释 + 表题行）落在渲染器内，
  规则以本契约为准；rows_text 由渲染器从网格预生成。

## 6. 与其他文档的关系

- 解析与择优链路（L0/L1/L2、验收网关）不变，本契约只规定**真身之下的交付层**。
- 分级交付（PRD 4.7.4）在散文件形态下成立（不再有打包形态，见 §4 修订注记）。
