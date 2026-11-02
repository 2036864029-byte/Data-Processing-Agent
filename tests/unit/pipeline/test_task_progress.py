"""任务进度写入测试：runner 低频写入、best-effort、worker 批入口。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dataagent.pipeline.task.factory import create_builtin_runner
from dataagent.platform.store.sql.tasks import (
    InMemoryTaskProgressStore,
    TaskProgress,
)


def dense_lines(prefix: str, count: int = 60) -> list[str]:
    return [
        f"{prefix} paragraph {index:03d} with supporting text"
        for index in range(count)
    ]


class RecordingProgress:
    """保留全部写入（InMemory store 只保留每任务最新状态）。"""

    def __init__(self) -> None:
        self.items: list[TaskProgress] = []

    def upsert(self, progress: TaskProgress) -> None:
        self.items.append(progress)

    def recent(self, limit: int = 50) -> list[TaskProgress]:
        return list(reversed(self.items))[:limit]


def test_runner_writes_start_progress_and_finish(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    store = RecordingProgress()
    dense = write_pdf(tmp_path / "dense.pdf", [dense_lines("alpha")])
    sparse = write_pdf(tmp_path / "sparse.pdf", [["hi"]])
    runner = create_builtin_runner(
        output_dir=tmp_path / "out", progress=store
    )

    runner.run([dense, sparse], task_id="t-progress")

    finished = store.items[-1]
    assert finished.task_id == "t-progress"
    assert finished.state == "finished"
    assert finished.pages_total == 2
    assert finished.pages_done == 2
    running = [item for item in store.items if item.state == "running"]
    assert any(item.pages_done == 1 and item.pages_total == 2 for item in running)
    assert store.items[0].progress == "0/0"


def test_process_pages_writes_finished_progress(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    store = InMemoryTaskProgressStore()
    sample = write_pdf(tmp_path / "one.pdf", [dense_lines("alpha")])
    runner = create_builtin_runner(
        output_dir=tmp_path / "out", progress=store
    )

    runner.process_pages(sample, [1], task_id="t-batch")

    finished = store.recent()[0]
    assert finished.state == "finished"
    assert finished.pages_done == 1
    assert finished.detail["status"] == "processed"


def test_progress_write_failure_does_not_break_task(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    class BrokenStore:
        def upsert(self, progress: TaskProgress) -> None:
            raise RuntimeError("database down")

        def recent(self, limit: int = 50) -> list[TaskProgress]:
            return []

    sample = write_pdf(tmp_path / "dense.pdf", [dense_lines("alpha")])
    runner = create_builtin_runner(
        output_dir=tmp_path / "out", progress=BrokenStore()
    )

    outcome = runner.run([sample], task_id="t-broken")

    assert outcome.count("processed") == 1
