"""性能基准测试（T127）：百分位口径、文件打开预算、OOM 降批、真实样本门禁。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dataagent.pipeline.bench import (
    estimate_file_opens,
    file_open_budget,
    percentile,
    run_bench,
)
from dataagent.platform.runtime.resources import OOM_BATCH_LADDER, MachineProfile

pytestmark = pytest.mark.bench


def dense_lines(prefix: str, count: int = 40) -> list[str]:
    return [
        f"{prefix} paragraph {index:03d} with supporting text" for index in range(count)
    ]


def test_percentile_nearest_rank() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 0.50) == 20.0
    assert percentile(values, 0.95) == 40.0


def test_file_open_budget_matches_formula() -> None:
    assert file_open_budget(100, 32) == 6
    assert estimate_file_opens(100, 32) == 5
    assert file_open_budget(32, 32) == 3


def test_run_bench_reports_gates(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    sample = write_pdf(
        tmp_path / "sample.pdf", [dense_lines("alpha"), dense_lines("bravo")]
    )

    report = run_bench(
        sample,
        repeats=1,
        machine=MachineProfile(cpu_cores=8, available_memory_mb=32_000),
    )

    assert report.pages == 2
    assert report.samples == 2
    assert report.p50_ms > 0
    assert report.p95_ms >= report.p50_ms
    assert report.oom_ladder == OOM_BATCH_LADDER
    assert report.file_opens_estimated <= report.file_open_budget
    gate_names = {gate.name for gate in report.gates}
    assert "单页 p50 ≤ 120ms" in gate_names
    assert "吞吐 ≥ 30 万页/日" in gate_names
    throughput = next(
        gate for gate in report.gates if gate.name == "吞吐 ≥ 30 万页/日"
    )
    assert throughput.ok is None  # 非 16C 机器不判吞吐
    assert "[OK]" in "\n".join(report.lines())


def test_run_bench_requires_existing_sample(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_bench(tmp_path / "missing.pdf")
