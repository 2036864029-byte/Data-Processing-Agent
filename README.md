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
├── pipeline/      # 处理阶段（P0-B 填充）
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
