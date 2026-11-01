"""端到端任务编排器测试（T125 前奏）：B1–B9 全链路串联。

覆盖：混合语料、内容去重、损坏/未分类拦截、按内容判定后缀、
PDF 多页输出、缺口页分级交付、Plan 探路→决策→批量、worker 批入口。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dataagent.kernel.plan import parse_plan
from dataagent.pipeline.task_runner import (
    TaskConfig,
    create_builtin_runner,
)


def dense_lines(prefix: str, count: int = 60) -> list[str]:
    return [
        f"{prefix} paragraph {index:03d} with supporting text" for index in range(count)
    ]


@pytest.fixture()
def runner(tmp_path: Path):
    return create_builtin_runner(output_dir=tmp_path / "out")


def test_mixed_folder_all_documents_reach_output(
    tmp_path: Path,
    runner,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    (tmp_path / "notes.txt").write_bytes(b"plain text document\nsecond line\n")
    (tmp_path / "table.csv").write_bytes(b"name,value\nalpha,1\nbeta,2\n")
    write_pdf(tmp_path / "book.pdf", [dense_lines("alpha"), dense_lines("bravo")])

    outcome = runner.run(
        sorted(tmp_path.glob("*.*")),
        task_id="mixed",
    )

    assert outcome.count("processed") == 3
    assert outcome.stats.pages_processed == 4
    assert outcome.stats.pages_failed == 0
    assert outcome.report.overview.total_docs == 3
    assert outcome.report.overview.total_pages == 4
    for document in outcome.documents:
        for output in document.outputs:
            assert Path(output).is_file()


def test_duplicate_content_reuses_existing_result(tmp_path: Path, runner) -> None:
    payload = b"duplicated content\n"
    (tmp_path / "a.txt").write_bytes(payload)
    (tmp_path / "b.txt").write_bytes(payload)

    outcome = runner.run([tmp_path / "a.txt", tmp_path / "b.txt"], task_id="dup")

    assert outcome.count("processed") == 1
    duplicate = next(item for item in outcome.documents if item.status == "duplicate")
    original = next(item for item in outcome.documents if item.status == "processed")
    assert duplicate.outputs == original.outputs
    assert duplicate.note


def test_unclassifiable_file_goes_pending_without_blocking(tmp_path: Path, runner) -> None:
    (tmp_path / "broken.bin").write_bytes(bytes(range(1, 32)) * 4)
    (tmp_path / "ok.txt").write_bytes(b"still processed\n")

    outcome = runner.run([tmp_path / "broken.bin", tmp_path / "ok.txt"], task_id="bad")

    assert outcome.count("processed") == 1
    assert outcome.count("pending") + outcome.count("rejected") == 1
    assert outcome.pending


def test_extension_mismatch_routed_by_content(tmp_path: Path, runner) -> None:
    disguised = tmp_path / "data.pdf"
    disguised.write_bytes(b"name,value\nalpha,1\nbeta,2\ngamma,3\n")

    outcome = runner.run([disguised], task_id="mismatch")

    document = outcome.documents[0]
    assert document.status == "processed"
    assert document.family == "tabular"


def test_pdf_pages_are_merged_and_written(
    tmp_path: Path,
    runner,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    write_pdf(
        tmp_path / "two.pdf",
        [dense_lines("first"), dense_lines("second")],
    )

    outcome = runner.run([tmp_path / "two.pdf"], task_id="pdf")
    document = outcome.documents[0]

    assert document.pages == 2
    markdown = Path(document.outputs[0]).read_text(encoding="utf-8")
    assert "first paragraph 000" in markdown
    assert "second paragraph 000" in markdown


def test_gap_page_delivers_needs_review_and_appears_in_report(
    tmp_path: Path,
    runner,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    write_pdf(tmp_path / "sparse.pdf", [["hi"]])

    outcome = runner.run([tmp_path / "sparse.pdf"], task_id="gap")
    document = outcome.documents[0]

    assert document.status == "processed"
    assert document.gap_pages == 1
    assert document.level is not None
    assert document.level.value == "needs_review"
    assert document.defect_codes
    assert outcome.report.files
    assert outcome.report.detail(document.doc_id)


def test_plan_probe_then_decision_then_bulk(tmp_path: Path, runner) -> None:
    (tmp_path / "one.txt").write_bytes(b"alpha document\n")
    (tmp_path / "two.txt").write_bytes(b"bravo document\n")
    plan = parse_plan(
        {
            "version": 1,
            "stages": [
                {
                    "id": "probe",
                    "kind": "processing",
                    "scope": {"pages": "1-1"},
                    "strategy": "quality",
                },
                {
                    "id": "decide",
                    "kind": "decision",
                    "on": {"metric": "stp_rate", "window": "probe"},
                    "branches": [
                        {
                            "when": {">=": [{"var": "stp_rate"}, 0.9]},
                            "goto": "bulk_fast",
                        },
                        {"else": True, "goto": "bulk_safe"},
                    ],
                },
                {
                    "id": "bulk_fast",
                    "kind": "processing",
                    "scope": {"pages": "2-"},
                    "strategy": "fast",
                },
                {
                    "id": "bulk_safe",
                    "kind": "processing",
                    "scope": {"pages": "2-"},
                    "strategy": "quality",
                },
            ],
            "guards": {"max_model_calls": 8, "max_replans": 3, "max_wall_time_s": 3600},
        }
    )
    planned = create_builtin_runner(
        output_dir=tmp_path / "out",
        config=TaskConfig(plan=plan),
    )

    outcome = planned.run(
        [tmp_path / "one.txt", tmp_path / "two.txt"], task_id="plan"
    )

    assert outcome.decisions[0].branch_taken == "bulk_fast"
    assert [stage.id for stage in outcome.plan_resolution.stages] == [
        "probe",
        "bulk_fast",
    ]
    assert outcome.stats.pages_processed == 2
    assert outcome.stats.stopped_by is None


def test_process_pages_worker_entry(
    tmp_path: Path,
    runner,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    write_pdf(
        tmp_path / "book.pdf",
        [dense_lines("alpha"), dense_lines("bravo")],
    )

    outcome = runner.process_pages(tmp_path / "book.pdf", [1], task_id="batch")

    assert outcome.status == "processed"
    assert outcome.pages == 1
    assert Path(outcome.outputs[0]).is_file()
