# Contract: TaskSpec（任务规格）

**来源**: PRD 4.1.4 | **Plan**: plan.md | **用途**: 任务创建的输入，Agent 编译链路与确定性连接器的中间产物

## Schema（YAML）

```yaml
task:
  source:
    type: folder | upload | api | db
    connection: <连接器 ID>
    path: /2024/annual_reports
    filter: "*.pdf"
    since: 2024-01-01
  scope:                      # 页级筛选
    page_filter: "has_table == true and pdf_type == 'text_based'"
    skip_pages: "is_toc == true"
  target:
    format: json
    schema: financial_report_v2
  quality:
    rulepack: cn_a_share_annual_v1
    stp_target: 0.85
    escalate_on: [table_break, low_extract_quality]
  budget:
    max_cost_per_doc: 0.5
    max_attempts_per_page: 3
    max_wall_time_s: 600
  profile_speed: quality | balanced | fast
  fallback:
    enhancer: local_ocr | mineru | none     # 默认 local_ocr
    on_unavailable: mark_only | fail_doc    # 默认 mark_only
  delivery:
    gap_tolerance: 0.05
  resources:
    profile: auto | 8c16g | 16c64g | 32c128g | custom
    workers: auto
    batch_pages: auto
  autonomy: copilot | autopilot
```

## 校验规则（不满足即拒绝创建）

1. 必须通过 JSON Schema 校验；生成后先预览（文件列表 / 前 20 行），人工确认后才执行（L1 及以上）。
2. `scope.page_filter` 与 `skip_pages` 使用与 RulePack 同一套受限表达式引擎；白名单字段仅限 PageProfile 的 `class` / `layout` / `signals` 三组。
3. **禁止 `page_filter` 引用 `extract_quality`**（因果循环：被处理的页才有质量分，未处理的页永远没机会被判定为需要处理）。
4. `profile_speed` 不得影响 L1 校验：fast 档只允许削减 L2 抽样比例与增强轮次。
5. `fallback.enhancer` 必须是配置项，不得写死；未接外部增强器时默认 `local_ocr`，环境不具备则落 `mark_only` 并在报告显式标注。
6. `resources` 由启动自检给出建议值，用户可覆盖，但覆盖后必须做可行性校验；超出机器能力直接告警降级，不允许"改完偷偷 OOM"。

## 派生数据（由 TaskSpec 生成）

- 幂等键 = `hash(文件) + 页范围 + 引擎 + 参数 + 规则包版本`
- 批次划分 = 按批大小（默认 32）切分页范围；OCR 页单独成批
