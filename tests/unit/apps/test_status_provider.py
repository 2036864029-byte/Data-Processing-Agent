"""StatusProvider 测试：从任务进度存储读取；库不可用时返回提示不抛错。"""

from __future__ import annotations

from dataagent.apps.api.status import SqlStatusProvider, render_status
from dataagent.platform.store.sql.tasks import (
    InMemoryTaskProgressStore,
    TaskProgress,
)


def test_status_provider_reads_task_progress() -> None:
    store = InMemoryTaskProgressStore()
    store.upsert(TaskProgress("task-1", "running", "3/10", 10, 3))
    store.upsert(TaskProgress("task-2", "finished", "4/4", 4, 4))

    status = SqlStatusProvider(store).snapshot()

    assert [(task.task_id, task.state, task.progress) for task in status.tasks] == [
        ("task-2", "finished", "4/4"),
        ("task-1", "running", "3/10"),
    ]
    assert status.note == ""
    assert "task-2" in render_status(status)


def test_status_provider_reports_store_failure() -> None:
    class BrokenStore:
        def upsert(self, progress: TaskProgress) -> None:
            raise AssertionError("不应写入")

        def recent(self, limit: int = 50) -> list[TaskProgress]:
            raise RuntimeError("connection refused")

    status = SqlStatusProvider(BrokenStore()).snapshot()

    assert status.tasks == ()
    assert "状态库不可用" in status.note
    assert "状态库不可用" in render_status(status)
