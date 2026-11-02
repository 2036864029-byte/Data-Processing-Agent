"""runner 接入 kernel 状态机：合法路径与终态（P0 任务级投影）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dataagent.kernel.state import State
from dataagent.kernel.supervisor import InMemoryProgressRepository
from dataagent.pipeline.repair.fallback import FallbackConfig, FallbackPath
from dataagent.pipeline.task.factory import create_builtin_runner
from dataagent.pipeline.task.models import TaskConfig


def dense_lines(prefix: str, count: int = 60) -> list[str]:
    return [
        f"{prefix} paragraph {index:03d} with supporting text"
        for index in range(count)
    ]


def test_clean_task_reaches_published(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    repository = InMemoryProgressRepository()
    sample = write_pdf(tmp_path / "dense.pdf", [dense_lines("alpha")])
    runner = create_builtin_runner(
        output_dir=tmp_path / "out", state_repository=repository
    )

    outcome = runner.run([sample], task_id="sm-clean")

    assert outcome.machine_state == "PUBLISHED"
    assert repository.state("sm-clean") is State.PUBLISHED


def test_gap_task_runs_repair_path_then_publishes(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    repository = InMemoryProgressRepository()
    sample = write_pdf(tmp_path / "sparse.pdf", [["hi"]])
    runner = create_builtin_runner(
        output_dir=tmp_path / "out", state_repository=repository
    )

    outcome = runner.run([sample], task_id="sm-gap")

    assert outcome.stats.pages_failed == 1
    assert outcome.machine_state == "PUBLISHED"


def test_no_usable_document_reaches_rejected(tmp_path: Path) -> None:
    repository = InMemoryProgressRepository()
    sample = tmp_path / "broken.bin"
    sample.write_bytes(bytes(range(0, 16)) * 8)
    runner = create_builtin_runner(
        output_dir=tmp_path / "out", state_repository=repository
    )

    outcome = runner.run([sample], task_id="sm-rejected")

    assert outcome.machine_state == "REJECTED"
    assert repository.state("sm-rejected") is State.REJECTED


def test_fail_doc_fallback_reaches_escalated(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    repository = InMemoryProgressRepository()
    sample = write_pdf(tmp_path / "sparse.pdf", [["hi"]])
    runner = create_builtin_runner(
        output_dir=tmp_path / "out",
        state_repository=repository,
        config=TaskConfig(
            fallback=FallbackConfig(on_unavailable=FallbackPath.FAIL_DOC)
        ),
    )

    outcome = runner.run([sample], task_id="sm-escalated")

    assert outcome.documents[0].status == "failed"
    assert outcome.machine_state == "ESCALATED"
    assert repository.state("sm-escalated") is State.ESCALATED


def test_process_pages_uses_state_machine(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    repository = InMemoryProgressRepository()
    sample = write_pdf(tmp_path / "dense.pdf", [dense_lines("alpha")])
    runner = create_builtin_runner(
        output_dir=tmp_path / "out", state_repository=repository
    )

    runner.process_pages(sample, [1], task_id="sm-batch")

    assert repository.state("sm-batch") is State.PUBLISHED
