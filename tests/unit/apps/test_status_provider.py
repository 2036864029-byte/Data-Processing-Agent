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


def test_status_provider_reads_queue_and_workers() -> None:
    class FakeQueue:
        def depth(self) -> int:
            return 7

    class FakeWorkers:
        def alive(self) -> int:
            return 3

    store = InMemoryTaskProgressStore()
    status = SqlStatusProvider(
        store, queue=FakeQueue(), workers=FakeWorkers()
    ).snapshot()

    assert status.queue_depth == 7
    assert status.alive_workers == 3
    assert status.note == ""


def test_status_provider_degrades_when_sources_fail() -> None:
    class BrokenQueue:
        def depth(self) -> int:
            raise RuntimeError("redis down")

    class BrokenWorkers:
        def alive(self) -> int:
            raise RuntimeError("redis down")

    status = SqlStatusProvider(
        InMemoryTaskProgressStore(),
        queue=BrokenQueue(),
        workers=BrokenWorkers(),
    ).snapshot()

    assert status.queue_depth == 0
    assert status.alive_workers == 0
    assert "队列深度不可用" in status.note
    assert "worker 存活数不可用" in status.note
