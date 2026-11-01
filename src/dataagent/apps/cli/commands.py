"""CLI 子命令实现：doctor / plugin / logs / status / bench。

doctor 对端口占用、磁盘不足、模型缺失给出明确修复建议（US5 验收）。
"""

from __future__ import annotations

import shutil
import socket
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from dataagent.apps.api.status import EmptyStatusProvider, StatusProvider
from dataagent.pipeline.bench import detect_machine, run_bench
from dataagent.platform.plugins.loader import PluginLoadError, discover_manifests
from dataagent.platform.runtime.resources import (
    recommended_batch_pages,
    recommended_workers,
)

DEFAULT_MIN_FREE_MB = 512
DEFAULT_MODEL_FILES: tuple[str, ...] = ("ocr/models/ch_PP-OCRv4_det.onnx",)


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    fix: str | None = None


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if not check.ok)


def check_port(host: str, port: int) -> CheckResult:
    """端口可用性（doctor 与 up 自检共用）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            return CheckResult(
                name="api_port",
                ok=False,
                detail=f"{host}:{port} 不可用（{exc}）",
                fix=(
                    "端口被占用：释放占用进程，或用 "
                    "`dataagent up --port <其他端口>` / 修改 config.yaml 的 api_port"
                ),
            )
    return CheckResult(name="api_port", ok=True, detail=f"{host}:{port} 可用")


def _check_disk(data_dir: Path, min_free_mb: int) -> CheckResult:
    target = data_dir
    while not target.exists() and target != target.parent:
        target = target.parent
    free_mb = shutil.disk_usage(target).free // (1024 * 1024)
    ok = free_mb >= min_free_mb
    return CheckResult(
        name="disk",
        ok=ok,
        detail=f"可用 {free_mb} MiB（要求 ≥ {min_free_mb}）",
        fix=None
        if ok
        else (
            "磁盘余量不足：清理磁盘，或把 config.yaml 的 local_store_root "
            "指向更大分区"
        ),
    )


def _check_models(model_dir: Path, model_files: Sequence[str]) -> CheckResult:
    missing = [name for name in model_files if not (model_dir / name).is_file()]
    if not missing:
        return CheckResult(name="models", ok=True, detail="模型文件完整")
    return CheckResult(
        name="models",
        ok=False,
        detail="缺失: " + ", ".join(missing),
        fix=(
            f"模型文件缺失：把模型包安装到 {model_dir}，"
            "或设置 fallback.enhancer=mark_only 显式降级"
        ),
    )


def run_doctor(
    *,
    api_port: int,
    data_dir: Path,
    model_dir: Path,
    min_free_mb: int = DEFAULT_MIN_FREE_MB,
    model_files: Sequence[str] = DEFAULT_MODEL_FILES,
    host: str = "127.0.0.1",
) -> DoctorReport:
    """环境自检：端口 / 磁盘 / 模型文件。"""
    return DoctorReport(
        checks=(
            check_port(host, api_port),
            _check_disk(data_dir, min_free_mb),
            _check_models(model_dir, model_files),
        )
    )


def list_plugins(plugin_root: Path) -> list[str]:
    """扫描插件目录并输出 `id@version  [capability, ...]` 行。"""
    if not plugin_root.is_dir():
        return []
    try:
        manifests = discover_manifests(plugin_root)
    except PluginLoadError as exc:
        return [f"插件目录不可用：{exc}"]
    return [
        f"{manifest.ref}  [{', '.join(p.capability for p in manifest.provides)}]"
        for manifest in manifests
    ]


def status_lines(provider: StatusProvider | None = None) -> list[str]:
    snapshot = (provider or EmptyStatusProvider()).snapshot()
    lines = [
        "DataAgent 状态",
        f"任务：{len(snapshot.tasks)}",
        f"队列深度：{snapshot.queue_depth}",
        f"存活 worker：{snapshot.alive_workers}",
    ]
    lines.extend(
        f"  {task.task_id}  {task.state}  {task.progress}" for task in snapshot.tasks
    )
    return lines


def bench_lines(
    sample: Path | None = None,
    *,
    repeats: int = 5,
    batch_pages: int | None = None,
) -> list[str]:
    """性能基准：无样本时打印用法与公式值；有样本时跑真实端到端基准。"""
    if sample is None:
        profile = detect_machine()
        return [
            "bench 需要样本：dataagent bench --sample <pdf>",
            (
                f"建议批大小 / worker: "
                f"{recommended_batch_pages(profile)} / {recommended_workers(profile)}"
            ),
            "单页 p50: n/a",
            "单页 p95: n/a",
            "批量吞吐: n/a",
            "内存峰值: n/a",
        ]
    return run_bench(sample, repeats=repeats, batch_pages=batch_pages).lines()


def bench_passed(lines: Sequence[str]) -> bool:
    return not any("[FAIL]" in line for line in lines)


def read_appended_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """读取 offset 之后的新增行，返回（行, 新 offset）。文件不存在返回空。"""
    if not path.is_file():
        return [], offset
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        lines = handle.readlines()
        return [line.rstrip("\n") for line in lines], handle.tell()


def read_last_lines(path: Path, count: int) -> list[str]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle.readlines()[-count:]]


def follow_logs(
    path: Path, stop: threading.Event, *, poll_interval_s: float = 0.5
) -> None:
    """`logs -f`：跟随文件新增行，直到 stop 置位。"""
    offset = 0
    while not stop.is_set():
        if path.is_file() and path.stat().st_size < offset:
            offset = 0  # 日志轮转 / 截断
        lines, offset = read_appended_lines(path, offset)
        for line in lines:
            print(line, flush=True)
        stop.wait(poll_interval_s)
