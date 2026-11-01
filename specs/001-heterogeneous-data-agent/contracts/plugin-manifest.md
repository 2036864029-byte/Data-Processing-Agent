# Contract: 插件 manifest 契约

**来源**: PRD 5.1 | **Plan**: plan.md | **用途**: 所有格式解析与处理能力以插件形式提供，遵循统一契约

## Schema（YAML）

```yaml
manifest:
  id: engine.pdf
  name: PDF 解析与分类插件
  version: 1.0.0
  api_version: 1
  kind: engine                # engine | validator | extractor | connector | exporter
  provides:                   # 能力签名结构化
    - capability: pdf.classify
      input:  { schema: doc.bytes }
      output: { schema: pdf.classification.v1 }
      latency: { p50_ms: 25, unit: document }
    - capability: pdf.text.extract
      input:  { schema: doc.bytes, params: page_range }
      output: { schema: page.artifact.v1 }
      latency: { p50_ms: 16, unit: page }
  cost_model:
    unit: page
    classify_ms_p50: 25       # 文档级分类，非页级
    cpu_ms_p50: 16            # 页级抽取
    cpu_ms_p95: 40
    gpu_ms: 0
    memory_mb: 200
  determinism: deterministic  # deterministic | stochastic | model
  concurrency: reentrant      # reentrant | exclusive | thread_unsafe
  trust: builtin              # builtin | signed | unsigned
  sandbox: none               # none | subprocess | container | remote
  limits:
    max_page_size_mb: 50
    timeout_s: 30
  batching:
    supported: true
    default_batch_pages: 32
    max_batch_pages: 50
    batch_affinity: document  # 同一文档的批必须同机
  optional_deps:
    - name: pdfium
      required_when: [pdf.ocr]
    - name: onnxruntime
      required_when: [pdf.ocr]
  compatibility:
    requires: []              # 内核 / API 版本、Python 版本、外部二进制最低版本
    conflicts: []
```

## 关键语义

| 字段 | 语义与消费方 |
| ---- | ---- |
| `provides[].capability` | 命名 `域.动作`；`*.classify` 供路由消费、`*.extract` 供解析消费，二者不交叉 |
| `determinism` | **决定能否重试 / 能否缓存**：仅 `deterministic` 允许结果缓存（缓存键 = 内容哈希 + 参数哈希 + `id@version`）；`stochastic / model` 禁缓存、每次计入成本 |
| `concurrency` | `exclusive` 的插件同一时刻只允许一个实例 |
| `trust` | `unsigned` 必须声明 `sandbox` 非 `none`，默认禁用、需显式开启 |
| `sandbox` | `none`（同进程）/ `subprocess`（子进程）/ `container` / `remote` |
| `cost_model.memory_mb` | 参与批大小与 worker 数计算 |

## 验收要求

1. 任何插件不符合该契约，不得上线。
2. `provides[]` 含 `*.classify` 的，其 classify 能力必须能在**不产内容**的前提下完成判定（不得内部偷偷全量抽取）。
3. 声明 `batching.supported: true` 的插件，必须支持一次调用传入多页、且保持调用方给定顺序。
4. `trust: unsigned` 必须 `sandbox` 非 `none`，默认禁用。
5. `determinism` 缺失或为 `stochastic / model` 的插件，不得启用结果缓存。

## engine.pdf 能力边界（复用 pdf-inspector）

| 能力 | 归属 | 契约说明 |
| ---- | ---- | ---- |
| 文本型/扫描/图片/混合分类、逐页 OCR 路由建议 + 机器可读原因、版式画像 | `pdf.classify` | 不产内容（`detect_pdf` 的 `markdown = None`、`cmap_gaps` 恒为空） |
| 位置/字体/字号/旋转/颜色/下划线/上下标/阅读顺序、Markdown、表格识别（Markdown 表）、编码缺口诊断 | `pdf.text.extract` | `cmap_gaps` 仅抽取后可用 |
| 表格 cell 级 bbox / 合并单元格 | ❌ 插件 | **自建后处理（T1）** |
| 跨页表续接、页眉页脚剔除 | ❌ 插件 | 自建 |
| 扫描件 OCR 执行 | ❌ 插件 | `engine.ocr` 插件（P1） |

**隐藏文本层（适配器行为）**：pdf-inspector 忽略扫描件中的不可见文本层（`invisible_text_layer` 页返回空）；`engine.pdf` 适配器可用 PDFium 探测并按质量门复用，不改变 `pdf.classify` / `pdf.text.extract` 的 capability 签名与输入输出 schema。


**页码基准铁律**：插件不同接口 / 同一返回对象内部页码基准不一致（0-indexed 与 1-indexed 混用），系统内一律 1-indexed，**字段级转换点全工程仅一处**（`contracts/pages.py`），断言式单测固定。
