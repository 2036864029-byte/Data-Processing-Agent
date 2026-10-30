# Quickstart: 端到端验证指南

**Created**: 2026-10-30 | **Plan**: plan.md | **用途**: 证明产品端到端可用；只写验证场景，不写实现细节

> 本文档是验证 / 运行指南。实现细节与任务拆分见 `/speckit-tasks` 产出的 `tasks.md`。契约与数据模型详见 `contracts/` 与 `data-model.md`，此处不重复。

## 前置条件

| 项 | 要求 | 说明 |
| ---- | ---- | ---- |
| 运行时 | Python 3.12 + `uv`（唯一包管理） | 环境搭建只允许 uv：`uv sync`（依据 `uv.lock`） |
| 容器 | Docker + Compose | 拉起 PostgreSQL / Redis / MinIO |
| 语料 | 本地 `doc/test_pdf/` 或自备样本 | 样本语料不入 git |
| 模型（OCR 路径） | 预置 `model_directory` + `offline=True` | 断网验证用；纯文本路径零外部依赖 |

## 安装与启动

```bash
uv sync                                   # 依 pyproject.toml + uv.lock
docker compose up -d                      # 后端：PG / Redis / MinIO
dataagent doctor                          # 环境自检（端口 / 磁盘 / 内存 / 模型）
dataagent up                              # 自检 → 建议配置 → 拉起 worker + API
# 已就绪 → http://127.0.0.1:8080
```

**预期**：`up` 输出资源自检结果与建议配置（worker / 批大小），各组件就绪，打印本地地址；`doctor` 对端口占用、磁盘不足、模型缺失给出明确修复建议。

## 验证场景

> 每个场景对应 spec 的 5 个用户故事，跑通即证明对应能力端到端可用。

### S1. 批量异构文档自动处理（spec US1）

```bash
# 准备 100 份混合样本（文本型 PDF / 扫描 PDF / CSV / TXT）到 <folder>
curl -X POST /tasks -d @taskspec.json        # taskspec: source.type=folder, target.format=json
curl /tasks/{id}                              # 轮询进度
curl /tasks/{id}/report                       # 任务总报告
```

**预期**：
- 所有文档均有输出与质量标记，报告含成功数 / 失败数 / 成本 / 耗时；
- 无文件被静默丢弃；重复提交复用已有结果（内容哈希去重）；
- 损坏 / 加密文件被拦截进待确认队列，任务其余文件不受影响；
- CSV 改名 `.pdf` 的文件按内容判定进 CSV 链路，并记录 `extension_mismatch`；
- 任务中途 `kill -9` worker，重启后从断点继续、已完成批不重做。

### S2. 复杂页标记、分级交付与人工协同（spec US2）

```bash
# 准备含跨页表格 / 编码损坏 / 扫描页的文档
curl /tasks/{id}/report
curl /tasks/{id}/diagnostic/{page}           # 失败页诊断卡
```

**预期**：
- 所有复杂页被标记为 gap，诊断卡含原因 + 位置 + 建议动作；
- 按 gap 页占比（0% / 3% / 8%）交付等级分别为 complete / partial_with_gaps / needs_review，needs_review 强制进人工队列；
- 未配置 OCR 增强器时按配置降级（默认仅标记），报告显式声明降级路径；
- 同一缺陷同页只修一次，无进展停机，无无限循环。

### S3. 规则配置生效与回滚（spec US3）

```bash
curl -X POST /rulepacks -d @rulepack.json    # 创建 + dry-run 报告
curl -X POST /rulepacks/{id}/versions        # 冻结生效
curl /tasks -d @taskspec_with_rulepack.json  # 新任务按新规则校验
curl -X POST /rulepacks/{id}/versions/rollback   # 回滚
```

**预期**：新规则 ≤ 5 分钟生效、无需重启；冲突规则发布被阻断；回滚立即生效且可审计；历史任务不受规则变更影响。

### S4. 质量与成本可度量（spec US4）

```bash
curl /tasks/{id}/report          # 1000 份文件任务
curl /tasks/{id}/results
```

**预期**：总报告 ≤ 10 KB（核心数字一页）、按原因聚合 ≤ 10 行、明细仅含"有 gap 的文件"；任取一文件可还原完整决策链（格式判定 → 路由理由 → 解析参数 → 质量指标 → 档位 → 修复动作）；"插件原因未知"占比 > 5% 触发告警且原始原因原样保留。

### S5. 部署自检与断网续跑（spec US5）

```bash
dataagent doctor                # 检出端口 / 磁盘 / 模型三类问题
dataagent status                # 进度查询（读 PG，非内存）
kill -9 <worker pid>            # 强杀验证
dataagent up                    # 重启后从 resume_point 继续
# 断网验证：预置模型 + offline=True，全链路跑通
```

**预期**：强杀 worker 后恢复 ≤ 30s、已完成批 0 重做；断网环境全链路（含 OCR 路径）可运行、数据不出域。

## 回归验证（宪法六个必写测试，随功能模块落地）

| # | 断言 | 模块 |
| -- | ---- | ---- |
| 1 | 页码基准：`extract_pages_markdown(path, pages=[k])` 后 `pages[0].page == k`，该页有表时 `k+1 in pages_with_tables` | extract/ |
| 2 | 未知原因透传：映射表外 reason → `plugin_reason_unknown`，`raw_reason` 原样保留 | repair/ |
| 3 | 算子不可变且幂等：原产物不变、两次调用候选一致 | repair/ |
| 4 | 无进展停机：`changed == false` 立即放弃、不重试第二次 | repair/ |
| 5 | 主键内容哈希 / 幂等键：锁定版本重跑历史任务，产物哈希一致 | store/ |
| 6 | 增强回路收敛：同码唯一、三层预算耗尽后升级、无无限循环 | repair/ |

## 性能与稳定性门禁（`dataagent bench`）

- 单进程峰值内存 ≤ 50 MB（批 32 页）；批大小 / worker 数按公式计算，禁写死 `cpu_count()`。
- 文件打开次数 ≤ ⌈页数/批大小⌉ + 2；强杀可续跑、坏文件不拖垮任务、OOM 自动降批（32→16→8）。
- 简单 PDF 单页 p50 ≤ 120ms、p95 ≤ 250ms（端到端，含分类 + L1 + 落库）。
