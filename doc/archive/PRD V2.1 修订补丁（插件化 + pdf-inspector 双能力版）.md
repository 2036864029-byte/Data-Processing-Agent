# PRD V2.1 修订补丁（插件化 + pdf-inspector 双能力版）

> 适用文档：`插件化异构数据处理 Agent 产品需求文档（PRD）V2.0.md`
> 本版替代上一版补丁（`PRD V2.1 修订补丁（pdf-inspector 内核版）.md`），修正其 3 处偏差，见 §0。

---

## 0. 上一版补丁的三处修正

| # | 上一版写法 | 问题 | 本版改为 |
|---|---|---|---|
| 1 | "pdf-inspector 不是插件，是 PDF 链路内核" | **错**。解析链路必须保持插件形态，否则新增 TXT / 图片解析器时无处挂载 | pdf-inspector **仍是一个插件**，只是声明**两个 capability**（分类 / 解析），被两个不同消费者调用 |
| 2 | "禁止 classify + process 两趟调用" | **错**。分类必须在选路之前发生，否则不知道该调哪个解析器——两趟是架构必需，不是浪费 | 改成：**预分类用轻量接口，解析用抽取接口**，两次调用职责不重叠、无重复工作 |
| 3 | 路由写成"一张平铺的链路表" | 漏了非 PDF 格式不该经过 pdf-inspector | 改成**两级路由**：一级格式判定（不调任何解析库）→ 二级仅 PDF 家族内页级路由 |

---

## 1. 两级路由（重写 4.2.2 / 4.2.3）

### 原文问题

V2.0 4.2.2 把链路平铺成一张表：

```
pdf_text / pdf_scanned / pdf_mixed / csv / txt / image
```

看不出层次，也表达不出"分类器只对 PDF 有效"这件事。结果是：研发要么对每个文件都调一次 PDF 分类器（对 CSV 白付开销），要么把格式判定和页级路由混在同一个函数里。

### 改为

#### 4.2.2 两级路由

**一级：边界处理与格式判定（确定性，不调用任何解析库）**

```
完整性检测 → magic bytes → 容器结构解析 → 家族归属
  ├─ 损坏 / 截断 / 加密 / 空文件 → 拦截，进待确认队列，带诊断码（4.1.6）
  └─ 家族：pdf | tabular | text | image | unknown → 进入二级
```

**为什么一级必须独立于任何解析库**

1. **损坏文件必须在进入解析器之前拦截。** 否则解析器抛出的异常会顶替掉诊断信息，人工看不到"哪一步失败"。
2. **非 PDF 格式不能用 PDF 分类器判。** pdf-inspector 只认 PDF，对 CSV 调用会得到一个"不是 PDF"的无用结论，还白付 10–50ms 启动开销。
3. **一级必须是纯规则。** 后缀名不可信，但 magic bytes + 结构特征可复现，且这一层的结果要写进审计。

**二级：仅 `pdf` 家族内，调 pdf-inspector 插件做页级路由**

| 输入 | 输出 | 用途 |
|---|---|---|
| `detect_pdf(path)` | `pdf_type`、`page_count`、`pages_needing_ocr`、`pages_with_tables`、`pages_with_columns`、`is_complex_layout`、`confidence`、`ocr_reasons_by_page` | **逐页分配链路** + 页面画像的 class/layout 分组 |

注意：`detect_pdf` **不做抽取、不产 Markdown**（`markdown = None`、`cmap_gaps` 恒为空）。所以它只承担路由与画像，不承担解析——这正是两级路由能省钱的原因。

**逐页分配规则**

```
detect_pdf 结果
  ├─ 页在 pages_needing_ocr 中        → OCR 插件（GPU 子链）
  ├─ 页不在 pages_needing_ocr 中      → PDF 文本解析插件（page 级入队）
  ├─ pdf_type == scanned（整本无文本层）→ 整本一次派给 OCR 插件，不逐页派
  ├─ pdf_type == image_based           → 图片解析插件
  └─ is_complex_layout == true         → 标记为复杂页，L1 硬门控按复杂页档位执行
```

**链路注册表（两级结构）**

| 级 | 链路 ID | 判定依据 | 引擎（插件） | 成本 |
|---|---|---|---|---|
| 一级 | `reject` | 完整性检测失败 | 无（拦截） | 0 |
| 一级 | `pdf.*` | `%PDF` magic + 页树可读 | 见二级 | — |
| 一级 | `tabular` | 分隔符一致性 > 0.8 | `engine.csv` | 极低 |
| 一级 | `text` | 可检测字符集 | `engine.text` | 极低 |
| 一级 | `image` | PNG/JPEG/TIFF magic | `engine.image` | 高 |
| 二级 | `pdf.native` | 页不在 `pages_needing_ocr` | `engine.pdf`（`pdf.text.extract`） | 极低（~10–25ms/页） |
| 二级 | `pdf.ocr` | 页在 `pages_needing_ocr` | `engine.ocr` | 高（2–5s/页） |
| 二级 | `pdf.vlm` | `ocr` 后仍 fail，或复杂页升级 | `engine.vlm` | 极高 |

**路由结果必须落库**：`{chain_id, page_range, engine, params, reason, profile_snapshot}`。`profile_snapshot` 是当时的 PageProfile 快照——因为画像本身就是路由依据，不存下来事后无法解释"为什么这页走了 OCR"。

---

## 2. 插件契约：允许一个插件声明多个 capability（补 4.3.2）

### 原文

4.3.2 只列了"统一契约 / 热插拔 / 多插件并存 / 版本化 / 资源声明 / 确定性声明"，没有说一个插件能不能同时提供"分类"和"解析"两类能力。

### 补充一条

| 要求 | 说明 |
|---|---|
| **能力多接口** | 一个插件可声明多个 `capability`，被不同消费者调用。**路由决策器只消费 `*.classify` 类能力，解析调度器只消费 `*.extract` 类能力**，两者不交叉 |

**pdf 插件的 manifest（示例）**

```yaml
manifest:
  id: engine.pdf
  name: PDF 解析与分类引擎
  version: 1.0.0
  api_version: 1
  kind: engine
  capabilities:
    - pdf.classify        # 供路由：pdf_type + 页级路由建议 + 版式画像（不产内容）
    - pdf.text.extract    # 供解析：页级 Markdown + text_items + 表格
  input:  { schema: doc.bytes }
  output: { schema: page.artifact.v1 }
  cost_model:
    unit: page
    classify_ms_p50: 25      # 文档级，非页级
    cpu_ms_p50: 16           # 页级抽取
    cpu_ms_p95: 40
    gpu_ms: 0
    memory_mb: 200
  determinism: deterministic
  sandbox: none
  limits:
    max_page_size_mb: 50
    timeout_s: 30
  optional_deps:
    - pdfium          # 仅 OCR 路径需要
    - onnxruntime     # 仅 OCR 路径需要
```

**这条设计的意义**：新增 TXT 解析器、图片解析器时，只需实现 `*.extract`；新增别的格式分类器时，只需实现 `*.classify`。PDF 之所以特殊，只是因为它一个插件实现了两类。插件契约本身不用改。

---

## 3. 4.3.3 重写：两次调用，各司其职

### 原文

> 选型理由：逐页分类 + 置信度 + 原生抽取 + 表格/阅读顺序，~150ms/页，CPU 友好，有 Python/Node/WASM 绑定。

### 改为

#### 4.3.3 PDF 解析插件（`engine.pdf`）

**定位**：注册在插件体系里的一个 `engine`，声明 `pdf.classify` 与 `pdf.text.extract` 两个能力。它不是唯一引擎，是 PDF 家族当前唯一的 `extract` 实现。

**调用模型：每文档 1 次分类 + N 次批量抽取**

| 阶段 | 接口 | 产出 | 调用次数 |
|---|---|---|---|
| 路由（二级） | `detect_pdf(path)` | `pdf_type`、`pages_needing_ocr`、`pages_with_tables`、`pages_with_columns`、`is_complex_layout`、`confidence` | **1 次 / 文档** |
| 解析（页级） | `extract_pages_markdown(path, pages=[...])` | 逐页 `markdown` + `needs_ocr` + `ocr_reason` | **⌈页数 / 批大小⌉ 次 / 文档** |
| 解析（需坐标） | `extract_text_with_positions(path, pages=[...])` | `text`/`bbox`/`font`/`font_size`/`rotation`/`fill_color`/`baseline_shift`/`mcid` | 同上 |
| 标题层级 | `extract_structure_elements(path)` | `(page, mcid, role)` → `H1..H6`/`P`/`Table` | 1 次（仅 tagged PDF） |
| 局部重抽（T2） | `extract_text_in_regions(path, page_regions)` | 区域文本 + 逐区域 `needs_ocr` | 按需 |

**为什么是"两次"而不是"很多次"——批量化是硬要求**

pdf-inspector 的工作模型是"打开文件 → 抽出指定页"。**逐页调用意味着逐页重新打开并解析同一份文件**：100 页文档会放大 100 倍的打开与页树解析开销，而真正的抽取只占其中很小一部分。

所以：

| 要求 | 值 |
|---|---|
| 同一文档的 native 页 | **必须批量抽取**，`pages` 一次传入 |
| 默认批大小 | 20–50 页/批（可按页大小与内存调整） |
| 并行粒度 | **批级并行**，不是页级 |
| 跨批顺序 | `extract_pages_markdown` 保持调用方顺序，合并时按 `page_no` 重排 |

> ⚠️ 这一条与 V2.0 4.3.4「页级：一页一个子任务，并行 fan-out」冲突，需要一并改，见 §4。

**性能口径（统一用此表，废弃 "~150ms/页"）**

| 场景 | 官方口径 | 折算（20 页/份） |
|---|---|---|
| 分类判定 `detect_pdf` | 10–50ms / 份 | 0.5–2.5ms / 页 |
| 文本型整本抽取 | <200ms / 份 | 10–25ms / 页 上限 |
| 基准语料 200 份端到端 | 0.470s / 份 | 23ms / 页 |

**预分类开销占比**：`0.5–2.5ms/页 ÷ 23ms/页 ≈ 2%–11%`。这是为"能选对解析器"付的必要成本，不是浪费。

**能力边界（必写，用于防止验收误判）**

| 能力 | `engine.pdf` | 归属 |
|---|---|---|
| 文本型 / 扫描件 / 图片型 / 混合 分类 | ✅ `pdf.classify` | 插件 |
| 逐页 OCR 路由建议 + 机器可读原因 | ✅ `pdf.classify` | 插件 |
| 版式画像信号（分栏、表格页、复杂度、旋转） | ✅ `pdf.classify` | 插件 |
| 位置、字体、字号、旋转、颜色、下划线、上下标 | ✅ `pdf.text.extract` | 插件 |
| 多栏阅读顺序、RTL | ✅ `pdf.text.extract` | 插件 |
| Markdown（标题/列表/代码/粗斜体/链接） | ✅ `pdf.text.extract` | 插件 |
| 编码缺口诊断 `cmap_gaps` | ✅（**仅抽取后可用**） | 插件 |
| 表格识别为 **Markdown 表格** | ✅ | 插件 |
| 表格 **cell 级 bbox / 合并单元格** | ❌ | **自建后处理（T1）** |
| 跨页表续接 | ❌ | 自建（4.7.1） |
| 页眉页脚剔除 | ❌ | 自建（T1） |
| 页码连续性判断 | ❌ | 自建（L3） |
| 扫描件 OCR 执行 | ❌（只路由） | `engine.ocr` 插件 |
| 扫描件逐页 OCR 置信度 | ❌ | `engine.ocr` 插件 |

**外部依赖（影响 4.8.4 离线交付与安装包体积）**

- 纯文本路径：零外部依赖，不加载任何模型——**成本优势的来源，验收需单独证明**
- OCR 路径：需 PDFium + ONNX Runtime 共享库（`PDFIUM_LIB_PATH` / `ORT_DYLIB_PATH`）；模型首次路由时下载并校验，**离线部署必须预置 `model_directory` + `offline=True`**
- 官方仅发布 **Python（PyO3）绑定**；无 Node / WASM 正式发布。Node 侧需自建 FFI 或包 Python 服务——这会影响部署形态选择，属技术文档内容，但 PRD 不能写错

---

## 4. 并行粒度修正（改 4.3.4）

| 层级 | 原文 | 改为 |
|---|---|---|
| 文件级 | 一个文件一个任务 | 保留 |
| 页级 | 一页一个子任务，并行 fan-out | **批级**：一批（20–50 页）一个子任务，批内不拆 |
| 分布式 | worker 无状态 | 保留 |
| 亲和性 | 同一文档的页尽量同机 | **同一文档的批必须同机**（否则每台都要重新打开文件、重新加载字体缓存） |

**理由**：解析库的成本主要不在"抽这一页"，而在"打开文件、解析页树、建字体缓存"。页级 fan-out 会把这份固定成本按页数倍数放大。批级 fan-out 保留并行度，同时把固定成本摊到 20–50 页上。

**例外**：OCR 页单独成批（成本结构不同，且需要 GPU 亲和），不与 native 页混批。

---

## 5. 页面画像与分级兜底（新增 4.3.5，原 4.3.4 顺延）

**页面画像是"分类结果"与"兜底决策"之间的合同。** 没有它，兜底只能靠"跑失败了换引擎"。

```yaml
page_profile:
  doc_id: <sha256>
  page_no: 1
  class:                          # 来自 pdf.classify（detect_pdf）
    pdf_type: text_based | scanned | image_based | mixed
    class_confidence: 0.0-1.0     # 文档级：分类判断有多确定
    route: native | ocr | vlm     # 逐页路由建议
    route_reason: [string]        # 机器可读，落库
  layout:                         # 来自 pdf.classify
    columns: int
    has_table: bool
    is_complex: bool
    rotation: 0 | 90 | 270
  signals:                        # 自建，从抽取结果推算
    char_density: float
    glyph_coverage: float         # (cmap_gaps.codes - cmap_gaps.unmapped) / cmap_gaps.codes
    empty_region_ratio: float
    header_footer_ratio: float
  quality:                        # 自建，抽取后才有
    extract_quality: 0.0-1.0
    tier: T0 | T1 | T2 | T3
```

**注意时序**：`class` 与 `layout` 在路由阶段就有（`detect_pdf` 产）；`signals` 与 `quality` 必须等抽取完成才有（`cmap_gaps` 在 detect 阶段恒为空）。所以 PageProfile 是**两次写入**：路由时写前两段，解析后补后两段。这也是为什么 4.2.3 要求存 `profile_snapshot`。

**分级兜底（四档）**

| 档 | 触发条件 | 动作 | 预算 |
|---|---|---|---|
| **T0 原样采用** | 无 warning，`extract_quality ≥ τ0` | 直接进 L1 | 0 |
| **T1 后处理修正** | 文本层完好，但表格 / 分栏 / 页眉页脚 / 层级有问题 | 在插件产物上加工：表格 cell 重建、页眉页脚剔除、跨页续接、标题层级修正 | 0（纯 CPU，毫秒级） |
| **T2 参数重跑** | 归因 = 参数不当 | 换参数或 `extract_text_in_regions` 局部重抽，`params_hash` 进幂等键 | 1 次 |
| **T3 换引擎** | 归因 = 插件能力边界外 | 换 `engine.ocr` / `engine.vlm` / 对照引擎 | 最高 |

**与 4.5.3 动作空间的映射**

| 档 | 动作 |
|---|---|
| T1 | `D resegment` + 新增 `H strip_header_footer`、`I rebuild_table_cells` |
| T2 | `B tune_params` |
| T3 | `A switch_engine`、`E ocr_fallback`、`F escalate` |

**三条产品要求**

1. **T1 是主力兜底，必须做厚。** 插件的短板是"输出 Markdown 但不管业务结构"（跨页、页眉页脚、cell bbox），这些恰好是纯 CPU 后处理能补的。T1 覆盖率高，STP 才上得去；靠 T3 提 STP，成本优势归零。
2. T0/T1 不消耗修复预算，T2 计 1 次，T3 计 2 次——预算按档位扣，不按"尝试次数"扣。
3. 档位判定必须确定可复现：同一 artifact + 同一画像 → 同一档位。**档位判定不准用模型**（模型只用于失败归因，见 2.2）。

---

## 6. 置信度拆成三个分数（改 4.3.6 PageArtifact 与 4.4.5）

### 原文问题

V2.0 把三类不同分数混成一个 `confidence`：

- 4.3.3 `confidence`（0–1）→ "决定是否进 L2"
- 4.3.5 `PageArtifact.confidence`（页级）
- 4.4.5 "低置信页（confidence < 0.85）→ 100% 抽样"

但插件给的 `confidence` 是**文档级**的"这是不是文本型 PDF"的判定置信度，**不反映抽取质量，也没有页级粒度**。照 V2.0 实现，研发只能造一个假分数。

### 改为

| 分数 | 层级 | 来源 | 回答什么问题 | 用途 |
|---|---|---|---|---|
| `class_confidence` | 文档 | `pdf.classify` | 我对"这是文本型"有多确定 | 低 → 整本转 T3；不做页级用途 |
| `extract_quality` | 页 | 自建（`glyph_coverage`、`char_density`、页框覆盖率、warning 数、`empty_region_ratio`） | 这一页抽得好不好 | **L1 门控、T1/T2 触发、抽样分层主力依据** |
| `fidelity` | 页 / 文档 | 需参考真值（抽样 OCR / 黄金集） | 离正确答案多远 | 验收指标、阈值标定、漂移监控。**不用于单页判定** |

**两处对应替换**

- 4.3.6 PageArtifact：`confidence: 0.0-1.0` → `class_confidence`（继承文档级）+ `extract_quality`（页级）
- 4.4.5 抽样表首行："低置信页（confidence < 0.85）100%" → 拆两行：
  - `extract_quality < 0.85` → 100% 抽样（页级风险）
  - `class_confidence < 0.85` → 整本转 T3（判不准类型，抽页无用）

---

## 7. 数值与目标修正

| 位置 | 原文 | 改为 |
|---|---|---|
| 4.2.2 `pdf_text` 成本 | 低（~150ms/页） | 极低（~10–25ms/页） |
| 6.1 简单 PDF 单页 | p50 ≤ 200ms，p95 ≤ 400ms | p50 ≤ **120ms**，p95 ≤ **250ms**（端到端含 L1 + 落库） |
| 6.1 单节点吞吐 | ≥ 10 万页/日，前提 ≤ 300ms/页 | ≥ **30 万页/日**，前提 ≤ 150ms/页 |
| 6.2 结构还原 | TEDS ≥ 0.85 | 拆两条：原生 ≥ **0.80**（基准 0.814）；含 T1 ≥ **0.85** |
| 6.2 阅读顺序 | ≥ 95% | ≥ 92% 原生 / ≥ 95%（含 T1）；基准 0.915 |
| 6.2 标题层级 | 无指标 | 新增：准确率 ≥ **0.78**（基准 0.788），tagged PDF 单独统计 |
| 9.4 简单页 | ~150ms，¥0.002–0.005 | ~30ms（含 L1），¥0.0005–0.001 |
| 5.1 manifest 示例 | `cpu_ms_p50: 150` | `classify_ms_p50: 25` + `cpu_ms_p50: 16` |

> 修正后加权单页成本显著低于原估算，1.3 与 9.3 的"全量 OCR 的 1/5"可上调为"1/10 以下"。**这是产品最硬的价值主张，不该被一个错误单位压低。**

---

## 8. P0 与里程碑调整

| 原 P0 项 | 处理 |
|---|---|
| 路由：PDF / CSV / TXT 三链路 | 改为**两级路由**（一级格式判定 + 二级 PDF 页级路由） |
| 解析：PDF 文本链路 + CSV + TXT | 改为**三个插件**：`engine.pdf`（双 capability）、`engine.csv`、`engine.text` |
| —— | **新增「页面画像」**：PageProfile 两段写入（路由段 + 质量段） |
| —— | **新增「分级兜底 T0–T3」**：档位判定 + 预算按档扣减 |
| —— | **新增「T1 后处理」**：表格 cell 重建、页眉页脚剔除、跨页续接（**P0 最大自建工作量，必须单列，否则会被误当"插件自带"**） |
| 修复：动作 A/B/D + 预算 | 改为 **T2（B）+ T3（A/E）+ 预算 + 诊断卡**；T1 并入后处理项 |
| 其余（质检 / 规则 / 合并 / 输出 / 评测） | 保留 |

**工作量结构变化**：格式判定与 PDF 解析几乎不用写（插件给），力气全转到 **T1 后处理 + 兜底档位判定 + 页面画像组装**。M1 可从 4 周收到 3 周。

**P2 统一后置前端**（V2.0 的交互形态散落在 3.1 / 4.5.6 / 4.9.2 / 4.9.4）：

> P0 / P1 只交付 API + CLI + 结果文件 + 处理报告，不含图形界面。所有 UI（对话界面、质量看板、待办队列、管理台）归 P2，第七章验收标准不含任何界面项。自然语言驱动能力在 P0 通过 TaskSpec API 与 RulePack 编译接口暴露，不依赖前端。

---

## 9. 页级筛选（补 4.1.4 TaskSpec）

有了 PageProfile 就该有下游消费方，否则画像白算。

```yaml
task:
  source: ...
  scope:
    page_filter: "has_table == true and pdf_type == 'text_based'"
    skip_pages: "is_toc == true"
```

**约束**：与 RulePack 共用受限表达式引擎（JSONLogic / CEL），白名单字段仅限 `class` / `layout` / `signals` 三组；**禁止引用 `extract_quality`**（否则形成因果循环：质量分决定了哪些页被处理，被处理的页才有质量分）。

自然语言落地：用户说"这批年报只处理带表格的页，目录页跳过" → 大模型编译为 `page_filter` → schema 校验 → 3 份样本 dry-run → 人工确认。

---

## 10. 附录 C 修正

| 用途 | 改为 |
|---|---|
| PDF 解析与分类 | **pdf-inspector（Rust，MIT）｜插件 `engine.pdf`，双 capability**：`pdf.classify`（分类 + 页级路由 + 版式画像）+ `pdf.text.extract`（位置感知抽取 + Markdown + 表格识别）。**仅 Python（PyO3）绑定，无 Node / WASM 正式发布** |
| —— | **新增**：表格结构增强｜自建（基于 `text_items` 的 bbox 重算行列与 cell）｜插件只输出 Markdown 表格，无 cell 坐标 |
| —— | **新增**：OCR 运行时｜PDFium + ONNX Runtime + PP-OCR 模型｜插件只做路由，不含模型与渲染后端，需预置离线包 |
| 对照引擎 | pdftotext / pdfminer / PyMuPDF｜保留（L2 跨引擎一致性校验） |

同时改掉 4.3.3 的事实错误："有 Python/Node/WASM 绑定" → "有 Python（PyO3）绑定"。

---

## 11. 风险登记册新增两条

| # | 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|---|
| **R9** | 定制后与上游分叉，升级成本随定制量线性上升 | 高 | 中 | **定制只做在外围后处理层，尽量不改插件源码**；必须改的集中在 `patches/` 并逐条记录原因；锁定版本 + 每季度评估上游升级；升级前跑黄金集回归 |
| **R10** | OCR 路径依赖 PDFium / ONNX Runtime 外部二进制，离线交付与国产化（麒麟/统信/ARM）可能无预编译包 | 中 | 高 | P1 前完成目标平台自编译验证；准备"OCR 路径不可用"的纯 native 降级方案并明确告知客户；模型随安装包分发并校验 checksum |

R9 是这份 PRD 最重要的新增：**成本优势全部来自"不改插件源码"**。为某个表格特性去改 Rust 源码，就同时失去上游升级能力和验证过的基准性能。

---

## 12. 不需要改的部分

| 章节 | 理由 |
|---|---|
| 一、产品概述（定位、二八原则、能力边界） | 与选型无关；"不做最强 PDF 解析器"这句因插件够强而更成立 |
| 二、Agent 能力边界（四角色、三处决策点） | "裁判不干活"与选型无关，V2.0 全文最好的一段 |
| 4.3.2 插件体系要求（除新增"能力多接口"） | 契约本质不变，只是允许一个插件多个 capability |
| 4.4 质检四层模型（除 4.4.5 抽样依据） | 体系成立，只换分数来源 |
| 4.6 RulePack / 4.7 合并 / 4.9 可观测 | 外围层，不受影响 |
| 5.1 插件契约 / 5.3 状态机 / 5.4 安全 | 契约仍需要；PDF 插件只是多声明一个 capability |
| 九、商业论证（除 9.4 数值） | 定位、竞品、护城河不受影响 |
| 十、风险主体（新增 R9/R10 除外） | 已有条目不受影响 |

---

## 13. 文档交付方式（回答"要不要合并"）

**结论：先合并成一份干净的 V2.1 全文，再交给 AI 写技术开发文档。不要拿"原文 + 补丁 + 口头说明"三个东西一起喂。**

理由：

1. 补丁里必然有"原文这段是错的"这类对立表述。两者一起喂，模型容易同时采纳两套说法，产出的技术文档会自相矛盾（比如一边写"每文档 1 次分类调用"，一边写"逐页 fan-out"）。
2. 补丁的写法是"差异说明"，不是"规格"。技术文档需要的是**无歧义的最终态**，不是变更过程。
3. 但**修订记录要留在 V2.1 内部**（V2.0 → V2.1 对照表，格式沿用 V2.0 里已有的"V1.0 → V2.0 修正对照"）。这样模型读到的是"历史沿革"，不是"当前有两套说法"。

**推荐链路**

```
V2.0 原文 ──合并──> PRD V2.1 全文（干净、无歧义、含修订记录）
                          │
                          ├──> 技术开发文档（喂 V2.1 全文，不喂补丁）
                          └──> 测试用例（同样喂 V2.1 全文）
```

**执行顺序**

1. **先改 §7 数值**（半天）：4.2.2 / 6.1 / 6.2 / 9.4 / 5.1。不涉及结构，但让所有下游讨论回到真实量级。
2. **再改 §6 分数三分**（半天）：解开"置信度到底指什么"的死结。
3. **再写 §1–§4**（1–2 天）：两级路由 + 能力多接口 + 4.3.3 重写 + 批级并行。这是重心，也是技术文档展开的基础。
4. **最后 §5、§8–§11**（半天）：页面画像、P0、页级筛选、附录、风险。
5. 技术文档在 §6 完成后即可启动，不必等全部定稿——因为技术文档要展开的正是"三个分数怎么算""T1 后处理怎么做""批大小怎么定"。

---

（补丁完）
