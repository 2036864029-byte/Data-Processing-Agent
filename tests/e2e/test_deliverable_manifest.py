"""交付清单对账端到端（PR-4-4）：manifest 与落盘产物双向一致。

真实流水线开 `emit_manifest` 后，清单里的每个 key 都必须有文件；
md/json/tables 下的每个产物都必须被清单引用。续跑跳过的文档仍须出现在清单中。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dataagent.pipeline.output.manifest import MANIFEST_NAME, verify_manifest
from dataagent.pipeline.task.config import TaskConfig
from dataagent.pipeline.task.factory import create_builtin_runner
from dataagent.pipeline.task.state_store import InMemoryTaskStateStore


def dense_lines(prefix: str, count: int = 60) -> list[str]:
    return [
        f"{prefix} paragraph {index:03d} with supporting text"
        for index in range(count)
    ]


def _md_output(outcome) -> Path:
    document = next(item for item in outcome.documents if item.status == "processed")
    return next(
        Path(path) for path in document.outputs if path.endswith(".md")
    )


def test_manifest_matches_landed_outputs(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    first = write_pdf(tmp_path / "a.pdf", [dense_lines("alpha")])
    second = write_pdf(tmp_path / "b.pdf", [dense_lines("bravo")])
    output_dir = tmp_path / "out"

    runner = create_builtin_runner(
        output_dir=output_dir, config=TaskConfig(emit_manifest=True)
    )
    runner.run([first, second], task_id="manifest-e2e")

    report = verify_manifest(output_dir)
    assert report.ok
    assert report.documents == 2
    assert report.keys_checked >= 4  # 每文档至少 md + json
    assert (output_dir / MANIFEST_NAME).is_file()


def test_resume_keeps_skipped_documents_in_manifest(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    store = InMemoryTaskStateStore()
    first = write_pdf(tmp_path / "a.pdf", [dense_lines("alpha")])
    second = write_pdf(tmp_path / "b.pdf", [dense_lines("bravo")])
    output_dir = tmp_path / "out"
    config = TaskConfig(emit_manifest=True)

    initial = create_builtin_runner(
        output_dir=output_dir, state_store=store, config=config
    ).run([first, second], task_id="manifest-resume")
    md_path = _md_output(initial)
    md_path.write_bytes(md_path.read_bytes() + b"\nSENTINEL\n")

    resumed = create_builtin_runner(
        output_dir=output_dir, state_store=store, config=config
    ).run([first, second], task_id="manifest-resume")

    assert "SENTINEL" in md_path.read_text(encoding="utf-8")
    report = verify_manifest(output_dir)
    assert report.ok
    assert report.documents == len(resumed.documents) == 2
