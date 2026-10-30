# Contract: CLI 命令 + HTTP API + 状态页

**来源**: PRD 4.8.8 / 3.1 | **Plan**: plan.md | **用途**: P0/P1 的全部交付入口；**CLI 是唯一入口，Web 由 CLI 拉起，二者同属一个进程**

> 宪法说明：端点与 CLI 命令清单属技术方案范围，不写入宪法；本契约是 plan 级清单，实现期可随迭代调整，但接口形态原则（CLI 唯一入口、API 无状态）不可违反。

## CLI（`dataagent` 子命令）

| 命令 | 功能 | 说明 |
| ---- | ---- | ---- |
| `dataagent up` | 拉起服务 | 自检（资源 / 端口 / 依赖）→ 启动后端（Redis / PG / MinIO 就绪）→ 启动 worker → 启动 API → 打印 `http://127.0.0.1:8080`；`dataagent up --config <path>` |
| `dataagent status` | 状态查询 | 任务列表、进度、gap 摘要、队列深度、worker 存活数 |
| `dataagent logs -f` | 日志跟踪 | 无头环境运维 |
| `dataagent plugin list` | 插件管理 | 能力索引 / 启停 / 版本 |
| `dataagent doctor` | 环境自检 | Docker 版本、端口占用、磁盘余量、内存、模型文件完整性、字体、外部二进制 |
| `dataagent bench` | 性能基准 | 单页 p50/p95、批量吞吐、内存峰值 |

## HTTP API

**形态原则**：API 无状态（所有状态进 PG，API 进程可随意重启 / 多起）；上传文件先进对象存储，不进 API 进程内存；≥200 并发状态查询 P95 < 200ms。

| 端点 | 方法 | 用途 |
| ---- | ---- | ---- |
| `/tasks` | POST | 创建任务（TaskSpec）→ 返回 task_id |
| `/tasks/{id}` | GET | 任务状态 + 进度 |
| `/tasks/{id}/report` | GET | 任务报告（分层：总览 / 聚合 / 清单 / 按需详情） |
| `/tasks/{id}/results` | GET | 结果文件（JSON / CSV / Markdown） |
| `/tasks/{id}/diagnostic/{page}` | GET | 诊断卡（失败页详情） |
| `/tasks` | GET | 任务列表（可筛选） |
| `/documents/{doc_id}` | GET | 文档详情（画像 / 缺陷 / gap） |
| `/rulepacks` | POST / GET | 规则包创建（含 dry-run）/ 列表 |
| `/rulepacks/{id}/versions` | POST / GET | 版本回滚 / 列表 |
| `/plugins` | GET | 插件列表（能力 / 版本 / 启停） |
| `/plugins/{id}` | PATCH | 启用 / 禁用 |
| `/status` | GET | **只读状态页**（服务端渲染 + 定时刷新，无构建链） |
| `/health` | GET | 健康检查 |

## 交付形态

| 形态 | 入口 | 适用 |
| ---- | ---- | ---- |
| **Docker Compose**（主推） | `docker compose up -d` | 绝大多数客户；同一份 compose 换外部后端地址即可迁移多机 |
| **pipx 单机** | `pipx install dataagent && dataagent up` | 无 Docker 的开发环境（P1） |
| **离线包** | 镜像 tar + wheel + 模型 + 字体 | 政企无外网（P1） |

## 五条必须从第一天守住的约束（单机多机同构）

1. API 无状态（所有状态进 PG）。
2. worker 无状态（任务归属靠队列，死了换一个接着干）。
3. Web 只做控制面（查进度读 PG，不读 worker 内存；上传文件先进对象存储）。
4. 配置只有一份（`.env` / `config.yaml`，单机多机同一 schema，差别只是值）。
5. 资源自检跑在启动时，建议值由机器规格推出，用户确认后写入运行配置。

## 无头环境

`dataagent status / logs -f / plugin list` 必须可用——SSH 进服务器是私有化部署的真实场景。
