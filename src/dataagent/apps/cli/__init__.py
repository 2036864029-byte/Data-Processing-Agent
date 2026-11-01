"""dataagent CLI（唯一入口）：up / status / logs / plugin / doctor / bench。

CLI 是唯一入口，Web 由 CLI 拉起（宪法 VI / cli-api.md）；无头环境
`status` / `logs -f` / `plugin list` 必须可用。
"""

from __future__ import annotations

import argparse
import contextlib
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from dataagent.apps.cli.commands import (
    DEFAULT_MIN_FREE_MB,
    bench_lines,
    bench_passed,
    check_port,
    follow_logs,
    list_plugins,
    read_last_lines,
    run_doctor,
    status_lines,
)
from dataagent.apps.cli.service import start_service
from dataagent.apps.worker.worker import ShellBatchHandler, Worker, WorkerConfig
from dataagent.platform.config import Settings, settings_from_yaml
from dataagent.platform.runtime.heartbeat import HeartbeatMonitor
from dataagent.platform.store.queue.redis import redis_queue_from_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dataagent", description="DataAgent 入口（CLI 唯一入口）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="自检 → 启动 API / worker → 打印地址")
    up.add_argument("--config", type=Path, default=None)
    up.add_argument("--host", default=None)
    up.add_argument("--port", type=int, default=None)
    up.add_argument("--role", choices=("all", "api", "worker"), default="all")

    sub.add_parser("status", help="任务列表 / 进度 / 队列深度 / worker 存活数")

    logs = sub.add_parser("logs", help="日志查看 / 跟踪")
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("--path", type=Path, default=Path("var/logs/dataagent.log"))
    logs.add_argument("--lines", type=int, default=50)

    plugin = sub.add_parser("plugin", help="插件管理")
    plugin_sub = plugin.add_subparsers(dest="plugin_command", required=True)
    plugin_list = plugin_sub.add_parser("list", help="能力索引 / 启停 / 版本")
    plugin_list.add_argument("--plugin-dir", type=Path, default=Path("var/plugins"))

    doctor = sub.add_parser("doctor", help="环境自检")
    doctor.add_argument("--data-dir", type=Path, default=Path("var"))
    doctor.add_argument("--model-dir", type=Path, default=Path("var/models"))
    doctor.add_argument("--min-free-mb", type=int, default=DEFAULT_MIN_FREE_MB)
    doctor.add_argument("--port", type=int, default=None)
    doctor.add_argument("--host", default="127.0.0.1")

    bench = sub.add_parser("bench", help="性能基准（p50 / p95 / 吞吐 / 内存峰值）")
    bench.add_argument("--sample", type=Path, default=None, help="基准 PDF 样本")
    bench.add_argument("--repeats", type=int, default=5, help="单页采样次数")
    bench.add_argument("--batch-pages", type=int, default=None, help="覆盖批大小")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "status":
        _print_lines(status_lines())
        return 0
    if args.command == "bench":
        lines = bench_lines(
            args.sample,
            repeats=args.repeats,
            batch_pages=args.batch_pages,
        )
        _print_lines(lines)
        return 0 if args.sample is None or bench_passed(lines) else 1
    if args.command == "logs":
        return _run_logs(args)
    if args.command == "plugin":
        lines = list_plugins(args.plugin_dir)
        _print_lines(lines or [f"无已注册插件（{args.plugin_dir}）"])
        return 0
    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "up":
        return _run_up(args)
    return 1


def _print_lines(lines: Sequence[str]) -> None:
    for line in lines:
        print(line)


def _run_logs(args: argparse.Namespace) -> int:
    if args.follow:
        stop = threading.Event()
        try:
            follow_logs(args.path, stop)
        except KeyboardInterrupt:
            stop.set()
        return 0
    lines = read_last_lines(args.path, args.lines)
    if not lines:
        print(f"日志文件不存在或为空：{args.path}")
    _print_lines(lines)
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    port = args.port if args.port is not None else Settings().api_port
    report = run_doctor(
        api_port=port,
        data_dir=args.data_dir,
        model_dir=args.model_dir,
        min_free_mb=args.min_free_mb,
        host=args.host,
    )
    for check in report.checks:
        print(f"[{'OK' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
        if check.fix is not None:
            print(f"  修复：{check.fix}")
    return 0 if report.ok else 1


def _run_up(args: argparse.Namespace) -> int:
    settings = settings_from_yaml(args.config) if args.config else Settings()
    host = args.host or settings.api_host
    port = args.port if args.port is not None else settings.api_port

    port_check = check_port(host, port)
    if not port_check.ok:
        print(f"[FAIL] {port_check.detail}")
        print(f"修复：{port_check.fix}")
        return 1

    if args.role == "worker":
        with contextlib.suppress(KeyboardInterrupt):
            _build_worker_target(settings)(threading.Event())
        return 0

    worker_target = _build_worker_target(settings) if args.role == "all" else None
    service = start_service(
        host=host, port=port, role=args.role, worker_target=worker_target
    )
    service.wait()
    return 0


def _build_worker_target(settings: Settings) -> Callable[[threading.Event], None]:
    def target(stop: threading.Event) -> None:
        queue = redis_queue_from_url(settings.redis_url, stream="dataagent:batches")
        worker = Worker(
            queue=queue,
            handler=ShellBatchHandler(),
            heartbeat=HeartbeatMonitor(),
            config=WorkerConfig(),
        )
        worker.run_forever(stop)

    return target
