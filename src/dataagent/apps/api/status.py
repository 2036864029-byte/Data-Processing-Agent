"""`/status` 只读页的状态数据与 SSR 渲染。

API 无状态：页面数据全部来自注入的 StatusProvider（生产实现读 PG），
API 进程不持有任务状态。页面用 meta refresh 定时刷新，无前端构建链。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Protocol


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


class StatusProvider(Protocol):
    """只读状态源：唯一方法为快照查询，无写路径。"""

    def snapshot(self) -> ServiceStatus: ...


class EmptyStatusProvider:
    """P0-A 骨架：无任务时的空状态源。"""

    def snapshot(self) -> ServiceStatus:
        return ServiceStatus()


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
</style>
</head>
<body>
<h1>DataAgent 状态</h1>
<p>队列深度：{status.queue_depth}　存活 worker：{status.alive_workers}</p>
<table>
<thead><tr><th>任务</th><th>状态</th><th>进度</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body>
</html>
"""
