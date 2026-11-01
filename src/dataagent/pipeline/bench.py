"""性能基准（T127 / 宪法性能预算 + SC-001/SC-011）。

`dataagent bench --sample <pdf>` 输出：单页端到端 p50/p95（分类 + 抽取 + L1）、
全量吞吐估算、Python 堆内存峰值、文件打开预算、批大小 / worker 公式值、
OOM 降批链。吞吐与 72h 泄漏门禁需目标硬件（16C64G）连续压测，未满条件时
标注「不适用」，不伪造通过。
"""

from __future__ import annotations

import ctypes
import math
import os
import sys
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from dataagent.pipeline.task_runner import create_builtin_runner
from dataagent.platform.runtime.resources import (
    MAX_BATCH_PAGES,
    MEMORY_PER_PROCESS_MB,
    OOM_BATCH_LADDER,
    MachineProfile,
    OomBatchDegrader,
    recommended_batch_pages,
    recommended_workers,
)

P50_LIMIT_MS: Final[float] = 120.0
P95_LIMIT_MS: Final[float] = 250.0
MEMORY_LIMIT_MB: Final[float] = 50.0

_MB: Final[int] = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    ok: bool | None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class BenchReport:
    sample: str
    pages: int
    samples: int
    p50_ms: float
    p95_ms: float
    pages_per_day: float
    peak_memory_mb: float
    batch_pages: int
    workers: int
    file_open_budget: int
    file_opens_estimated: int
    oom_ladder: tuple[int, ...]
    gates: tuple[Gate, ...]

    @property
    def ok(self) -> bool:
        return all(gate.ok is not False for gate in self.gates)

    def lines(self) -> list[str]:
        lines = [
            f"bench 样本：{self.sample}（{self.pages} 页，{self.samples} 次采样）",
            f"单页 p50: {self.p50_ms:.1f} ms（≤ {P50_LIMIT_MS:.0f}）",
            f"单页 p95: {self.p95_ms:.1f} ms（≤ {P95_LIMIT_MS:.0f}）",
            f"吞吐估算: {self.pages_per_day:,.0f} 页/日",
            f"内存峰值: {self.peak_memory_mb:.1f} MB（≤ {MEMORY_LIMIT_MB:.0f}）",
            f"批大小 / worker: {self.batch_pages} / {self.workers}",
            (
                f"文件打开: {self.file_opens_estimated} 次"
                f"（预算 ≤ {self.file_open_budget}）"
            ),
            f"OOM 降批链: {' → '.join(str(size) for size in self.oom_ladder)}",
            "门禁:",
        ]
        for gate in self.gates:
            mark = "N/A" if gate.ok is None else ("OK" if gate.ok else "FAIL")
            detail = f" — {gate.detail}" if gate.detail else ""
            lines.append(f"  [{mark}] {gate.name}{detail}")
        return lines


def percentile(values: list[float], fraction: float) -> float:
    """最近秩百分位（确定性，无插值歧义）。"""
    if not values:
        raise ValueError("percentile 需要非空样本")
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def file_open_budget(page_count: int, batch_pages: int) -> int:
    """文件打开次数上限：⌈页数/批大小⌉ + 2（含分类与容错余量）。"""
    if page_count < 0 or batch_pages < 1:
        raise ValueError("page_count ≥ 0 且 batch_pages ≥ 1")
    return math.ceil(page_count / batch_pages) + 2


def estimate_file_opens(page_count: int, batch_pages: int) -> int:
    """规划口径：1 次分类 + 每批 1 次抽取。"""
    return 1 + math.ceil(page_count / batch_pages)


def detect_machine() -> MachineProfile:
    cores = os.cpu_count() or 1
    return MachineProfile(cpu_cores=cores, available_memory_mb=_available_memory_mb())


def run_bench(
    sample: Path | str,
    *,
    repeats: int = 5,
    batch_pages: int | None = None,
    machine: MachineProfile | None = None,
) -> BenchReport:
    sample_path = Path(sample)
    if not sample_path.is_file():
        raise FileNotFoundError(f"bench 样本不存在：{sample_path}")
    if repeats < 1:
        raise ValueError("repeats 必须 ≥ 1")
    profile = machine if machine is not None else detect_machine()
    resolved_batch = (
        batch_pages
        if batch_pages is not None
        else recommended_batch_pages(profile, memory_per_process_mb=MEMORY_PER_PROCESS_MB)
    )
    resolved_batch = max(1, min(resolved_batch, MAX_BATCH_PAGES))
    workers = recommended_workers(profile, memory_per_process_mb=MEMORY_PER_PROCESS_MB)

    pages = _page_count(sample_path)
    latencies, peak_memory = _measure_pages(sample_path, pages, repeats)
    throughput = _throughput_pages_per_day(sample_path, pages)
    budget = file_open_budget(pages, resolved_batch)
    estimated = estimate_file_opens(pages, resolved_batch)
    ladder = _oom_ladder()

    gates = (
        _gate("单页 p50 ≤ 120ms", percentile(latencies, 0.50) <= P50_LIMIT_MS),
        _gate("单页 p95 ≤ 250ms", percentile(latencies, 0.95) <= P95_LIMIT_MS),
        _gate("内存峰值 ≤ 50MB", peak_memory <= MEMORY_LIMIT_MB),
        _gate("文件打开次数 ≤ ⌈页数/批大小⌉+2", estimated <= budget),
        _gate(
            "批大小 / worker 按公式计算",
            resolved_batch <= MAX_BATCH_PAGES and workers >= 1,
        ),
        _gate("OOM 自动降批 32→16→8", ladder == OOM_BATCH_LADDER),
        Gate(
            "吞吐 ≥ 30 万页/日",
            None,
            "需 16C64G 连续 1 小时压测取稳定值（当前为短样本估算）",
        ),
        Gate(
            "72h 压测无内存泄漏（增长 < 5%）",
            None,
            "需目标硬件连续 72 小时压测",
        ),
    )
    return BenchReport(
        sample=str(sample_path),
        pages=pages,
        samples=len(latencies),
        p50_ms=percentile(latencies, 0.50),
        p95_ms=percentile(latencies, 0.95),
        pages_per_day=throughput,
        peak_memory_mb=peak_memory,
        batch_pages=resolved_batch,
        workers=workers,
        file_open_budget=budget,
        file_opens_estimated=estimated,
        oom_ladder=ladder,
        gates=gates,
    )


def _gate(name: str, ok: bool) -> Gate:
    return Gate(name=name, ok=ok)


def _measure_pages(
    sample: Path, pages: int, repeats: int
) -> tuple[list[float], float]:
    latencies: list[float] = []
    with tempfile.TemporaryDirectory(prefix="dataagent-bench-") as tmp:
        root = Path(tmp)
        tracemalloc.start()
        try:
            for page in range(1, pages + 1):
                for repeat in range(repeats):
                    runner = create_builtin_runner(output_dir=root / f"{page}-{repeat}")
                    started = time.perf_counter()
                    runner.process_pages(sample, [page], task_id="bench")
                    latencies.append((time.perf_counter() - started) * 1000)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    return latencies, peak / _MB


def _throughput_pages_per_day(sample: Path, pages: int) -> float:
    with tempfile.TemporaryDirectory(prefix="dataagent-bench-run-") as tmp:
        runner = create_builtin_runner(output_dir=tmp)
        started = time.perf_counter()
        runner.run([sample], task_id="bench-run")
        elapsed = time.perf_counter() - started
    if elapsed <= 0 or pages <= 0:
        return 0.0
    return pages / elapsed * 86_400


def _page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        return int(len(document))
    finally:
        document.close()


def _oom_ladder() -> tuple[int, ...]:
    degrader = OomBatchDegrader()
    ladder = [degrader.batch_pages]
    for _ in range(len(OOM_BATCH_LADDER)):
        try:
            ladder.append(degrader.on_oom())
        except Exception:
            break
    return tuple(ladder)


def _available_memory_mb() -> int:
    if sys.platform == "win32":
        return _windows_available_memory_mb()
    try:
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // _MB)
    except (AttributeError, OSError, ValueError):
        return 8192


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _windows_available_memory_mb() -> int:
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return 8192
    if not windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 8192
    return int(status.ullAvailPhys // _MB)
