# DataAgent

插件化异构数据处理 Agent：一条确定性流水线，页级决策全部规则，任务级模型调用受控。
PDF 分类与抽取复用 pdf-inspector（`engine.pdf` 插件，双 capability）。

## 环境要求

| 项 | 要求 |
| ---- | ---- |
| 运行时 | Python 3.12 + [uv](https://docs.astral.sh/uv/)（唯一包管理） |
| 容器 | Docker Desktop / Docker Engine + Compose |
| 开发平台 | Windows / macOS；交付目标 Linux 服务器 |

## 快速开始

```bash
uv sync
docker compose -f deploy/docker-compose.yml up -d
uv run dataagent doctor
uv run dataagent up
# 打开 http://127.0.0.1:8080/status
```

**专用端口**（避免与本机其他 Docker 项目冲突）：

| 服务 | 宿主机端口 |
| ---- | ---- |
| API / `/status` | `8080`（可用 `DATAAGENT_API_PORT` 覆盖） |
| PostgreSQL | `127.0.0.1:15432` |
| Redis | `127.0.0.1:16379` |
| MinIO | `127.0.0.1:19000`（控制台 `19001`） |

compose 内部按服务名互连，不经过上述宿主机端口。详细端口规划见
`docs/ADR/` 与 `deploy/docker-compose.yml` 注释。

## CLI

```bash
dataagent up [--host H] [--port P] [--role all|api|worker] [--config PATH]
dataagent status
dataagent logs [-f] [--path PATH]
dataagent plugin list [--plugin-dir DIR]
dataagent doctor
dataagent bench
```

CLI 是唯一入口，Web 由 CLI 拉起；API 无状态、worker 无状态（任务归属靠队列）。

## P0-B 已落地能力

- 接入与体检：folder/upload、内容哈希去重、11 项三态体检、待确认队列
- 一级格式判定：五家族三态、后缀不符按内容判定、歧义双跑
- 两级路由：一级格式、二级仅 PDF 内分类；批级抽取 32 页、扫描页单独成批
- 页面画像：两段写入；隐藏文本层（PDFium）探测复用、按需 `process_pdf` 标题标定
- 质检：L1 全量逐页 + L2 分层抽样 + quality/balanced/fast 三档
- 修复：9 算子、reason 映射、同码唯一增强回路、失败页聚包、mark_only 降级
- 合并与交付：跨页表、缺口占位、complete/partial_with_gaps/needs_review
- 输出：JSON / CSV / Markdown / Excel + 数据库落库
- 规则：RulePack + 受限表达式 + 声明式字段提取

## 黄金集与评测（本地私有）

```bash
uv run python tests/eval/build_manifest.py          # 200 页六类清单
uv run python tests/eval/annotations_template.py    # 标注模板 + 60 页人工子集
uv run python tests/eval/prepare_review.py          # 左页图/右产物 对照查看器
uv run python tests/eval/collect_metrics.py         # 代理指标
uv run python tests/eval/evaluate.py                # 构造样本自动评测
uv run python tests/eval/calibrate.py               # 人工标注后标定
```

样本与标注写入 `var/golden/`（git 忽略）；隐藏文本层恢复样本与口径见
`docs/ADR/0005-hidden-text-layer.md`、`tests/eval/README.md`。

## 已知限制（Phase 11 集成）

- 端到端 task-level runner 尚未接线（B1–B8 组件已就绪，T125 未执行）
- B-PLAN（T120/T121）未实现；NL→Plan 场景待补
- `dataagent bench` 仍为骨架；吞吐 / 72h 压测需目标硬件执行
- 详见 `docs/ADR/0006-p0b-implementation-decisions.md`

## 测试与门禁

```bash
uv run ruff check .
uv run mypy src/dataagent
uv run pytest --cov-fail-under=80
uv run coverage report --fail-under=90 \
  --include="src/dataagent/kernel/**,src/dataagent/platform/store/**,src/dataagent/pipeline/repair/**"
PYTHONPATH=src uv run lint-imports
bash scripts/check-gates.sh
```

续跑演示（真实强杀进程，验证已完成批不重做）：

```bash
uv run pytest tests/e2e/test_resume_demo.py -q
```

## 目录结构

```text
src/dataagent/
├── contracts/     # 契约层：ids / errors / pages / defects / plugin / policies / messages
├── platform/      # 支撑设施：config / store / runtime / observe / plugins / rules
├── kernel/        # 内核：state / budget / registry / supervisor / policies
├── pipeline/      # ingest / sniff / route / profile / quality / repair / merge / output
└── apps/          # 进程入口：cli / api / worker
deploy/            # Dockerfile / compose / offline 包骨架
docs/              # CHANGELOG、ADR、回归盘点
specs/             # spec / plan / research / data-model / contracts / tasks
```

## 当前阶段

P0-A 基础支撑层（A1–A9）已完成；P0-B 功能模块层待启动。任务拆解见
`specs/001-heterogeneous-data-agent/tasks.md` 与 `tasks-p0-b.md`。

## 文档

- [CHANGELOG](docs/CHANGELOG.md)
- [回归必写测试盘点](docs/regression-inventory.md)
- [架构决策记录 ADR](docs/ADR/)
- [端到端验证指南](specs/001-heterogeneous-data-agent/quickstart.md)
