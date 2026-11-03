"""文档级续跑：已完成文档不重做，输入变化则整体重跑。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dataagent.pipeline.task.factory import create_builtin_runner
from dataagent.pipeline.task.state_store import InMemoryTaskStateStore


def dense_lines(prefix: str, count: int = 60) -> list[str]:
    return [
        f"{prefix} paragraph {index:03d} with supporting text"
        for index in range(count)
    ]


def _delete_first_output(outcome) -> Path:
    target = Path(outcome.documents[0].outputs[0])
    target.unlink()
    return target


def test_resume_skips_completed_documents(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    store = InMemoryTaskStateStore()
    first_path = write_pdf(tmp_path / "a.pdf", [dense_lines("alpha")])
    second_path = write_pdf(tmp_path / "b.pdf", [dense_lines("bravo")])
    output_dir = tmp_path / "out"

    first = create_builtin_runner(
        output_dir=output_dir, state_store=store
    ).run([first_path, second_path], task_id="resume-1")
    removed = _delete_first_output(first)

    resumed = create_builtin_runner(
        output_dir=output_dir, state_store=store
    ).run([first_path, second_path], task_id="resume-1")

    assert resumed.count("processed") == 2
    assert resumed.stats.pages_processed == 2
    assert not removed.exists()  # 已完成文档没有重做
    assert resumed.machine_state == "PUBLISHED"


def test_input_signature_change_resets_resume(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    store = InMemoryTaskStateStore()
    first_path = write_pdf(tmp_path / "a.pdf", [dense_lines("alpha")])
    second_path = write_pdf(tmp_path / "b.pdf", [dense_lines("bravo")])
    third_path = write_pdf(tmp_path / "c.pdf", [dense_lines("charlie")])
    output_dir = tmp_path / "out"

    first = create_builtin_runner(
        output_dir=output_dir, state_store=store
    ).run([first_path, second_path], task_id="resume-2")
    removed = _delete_first_output(first)

    create_builtin_runner(output_dir=output_dir, state_store=store).run(
        [first_path, second_path, third_path], task_id="resume-2"
    )

    assert removed.exists()  # 输入集合变化 → 整体重跑
