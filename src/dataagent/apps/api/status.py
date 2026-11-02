"""`/status` 只读页的状态数据与 SSR 渲染。

API 无状态：页面数据全部来自注入的 StatusProvider（生产实现读 PG），
API 进程不持有任务状态。页面用 meta refresh 定时刷新，无前端构建链。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Protocol

from dataagent.platform.store.sql.tasks import TaskProgressStore


@dataclass(frozen=True, slots=True)
class TaskStatus:
    task_id: str
    state: str
    progress: str


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    tasks: tuple[TaskStatus, ...] = ()
    queue_depth: int = 0
    alive_workers: int = 0
    note: str = ""


class StatusProvider(Protocol):
    """只读状态源：唯一方法为快照查询，无写路径。"""

    def snapshot(self) -> ServiceStatus: ...


class QueueDepthSource(Protocol):
    def depth(self) -> int: ...


class WorkerLivenessSource(Protocol):
    def alive(self) -> int: ...


class EmptyStatusProvider:
    """P0-A 骨架：无任务时的空状态源。"""

    def snapshot(self) -> ServiceStatus:
        return ServiceStatus()


class SqlStatusProvider:
    """从 PG `task_progress` 读取任务进度；队列 / worker 存活来自 Redis。"""

    def __init__(
        self,
        store: TaskProgressStore,
        *,
        queue: QueueDepthSource | None = None,
        workers: WorkerLivenessSource | None = None,
        limit: int = 50,
    ) -> None:
        self._store = store
        self._queue = queue
        self._workers = workers
        self._limit = limit

    def snapshot(self) -> ServiceStatus:
        notes: list[str] = []
        try:
            items = self._store.recent(self._limit)
        except Exception as exc:
            items = []
            notes.append(f"状态库不可用：{exc}")

        queue_depth = 0
        if self._queue is not None:
            try:
                queue_depth = self._queue.depth()
            except Exception as exc:
                notes.append(f"队列深度不可用：{exc}")

        alive_workers = 0
        if self._workers is not None:
            try:
                alive_workers = self._workers.alive()
            except Exception as exc:
                notes.append(f"worker 存活数不可用：{exc}")

        return ServiceStatus(
            tasks=tuple(
                TaskStatus(
                    task_id=item.task_id,
                    state=item.state,
                    progress=item.progress,
                )
                for item in items
            ),
            queue_depth=queue_depth,
            alive_workers=alive_workers,
            note="；".join(notes),
        )


def render_status(status: ServiceStatus) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(task.task_id)}</td>"
        f"<td>{escape(task.state)}</td>"
        f"<td>{escape(task.progress)}</td>"
        "</tr>"
        for task in status.tasks
    )
    if not rows:
        rows = '<tr><td colspan="3">暂无任务</td></tr>'
    note = f'<p class="note">{escape(status.note)}</p>' if status.note else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>DataAgent 状态</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1f2933; }}
table {{ border-collapse: collapse; margin-top: 1rem; }}
th, td {{ border: 1px solid #cbd2d9; padding: .4rem .8rem; text-align: left; }}
th {{ background: #f5f7fa; }}
.note {{ color: #b26a00; }}
</style>
</head>
<body>
<h1>DataAgent 状态</h1>
<p>队列深度：{status.queue_depth}　存活 worker：{status.alive_workers}</p>
{note}
<table>
<thead><tr><th>任务</th><th>状态</th><th>进度</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body>
</html>
"""
