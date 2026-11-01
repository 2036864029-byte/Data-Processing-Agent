"""CLI 契约测试（T044，A7）。

`dataagent up / status / logs -f / plugin list / doctor / bench` 存在且符合 cli-api.md；
doctor 对端口占用、磁盘不足、模型缺失给出明确修复建议；CLI 唯一入口。
"""

import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

from dataagent.apps.cli import build_parser, main
from dataagent.apps.cli.commands import (
    follow_logs,
    list_plugins,
    read_appended_lines,
    run_doctor,
)
from dataagent.apps.cli.service import start_service


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_manifest(root: Path) -> Path:
    plugin_dir = root / "pdf"
    plugin_dir.mkdir(parents=True)
    manifest = {
        "manifest": {
            "id": "engine.pdf",
            "name": "PDF",
            "version": "1.0.0",
            "api_version": 1,
            "kind": "engine",
            "provides": [
                {
                    "capability": "pdf.classify",
                    "input": {"schema": "doc.bytes"},
                    "output": {"schema": "out.v1"},
                    "latency": {"p50_ms": 1, "unit": "page"},
                }
            ],
            "determinism": "deterministic",
        }
    }
    (plugin_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8"
    )
    return root


def test_cli_exposes_all_contract_commands() -> None:
    """全部命令与 `logs -f`、`plugin list` 子命令可解析。"""
    parser = build_parser()
    for argv in (
        ["up"],
        ["status"],
        ["logs", "-f"],
        ["plugin", "list"],
        ["doctor"],
        ["bench"],
    ):
        assert parser.parse_args(argv) is not None


def test_doctor_reports_port_occupation_with_fix(tmp_path: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        occupied = int(sock.getsockname()[1])
        report = run_doctor(
            api_port=occupied,
            data_dir=tmp_path,
            model_dir=tmp_path,
            model_files=(),
            min_free_mb=0,
        )

    check = next(c for c in report.checks if c.name == "api_port")
    assert check.ok is False
    assert check.fix is not None and "端口" in check.fix


def test_doctor_reports_disk_shortage_with_fix(tmp_path: Path) -> None:
    report = run_doctor(
        api_port=free_port(),
        data_dir=tmp_path,
        model_dir=tmp_path,
        model_files=(),
        min_free_mb=10**9,
    )
    check = next(c for c in report.checks if c.name == "disk")
    assert check.ok is False
    assert check.fix is not None and "磁盘" in check.fix


def test_doctor_reports_missing_models_with_fix(tmp_path: Path) -> None:
    report = run_doctor(
        api_port=free_port(),
        data_dir=tmp_path,
        model_dir=tmp_path / "models",
        model_files=("ocr/model.onnx",),
        min_free_mb=0,
    )
    check = next(c for c in report.checks if c.name == "models")
    assert check.ok is False
    assert check.fix is not None and "模型" in check.fix


def test_doctor_main_returns_nonzero_and_prints_fix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "doctor",
            "--data-dir",
            str(tmp_path),
            "--model-dir",
            str(tmp_path / "missing"),
            "--min-free-mb",
            "0",
            "--port",
            str(free_port()),
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "修复" in out


def test_plugin_list_discovers_manifests(tmp_path: Path) -> None:
    root = write_manifest(tmp_path / "plugins")
    lines = list_plugins(root)

    assert any("engine.pdf@1.0.0" in line for line in lines)
    assert any("pdf.classify" in line for line in lines)


def test_plugin_list_cli_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = write_manifest(tmp_path / "plugins")
    assert main(["plugin", "list", "--plugin-dir", str(root)]) == 0
    assert "engine.pdf@1.0.0" in capsys.readouterr().out


def test_read_appended_lines_returns_only_new_lines(tmp_path: Path) -> None:
    log = tmp_path / "dataagent.log"
    log.write_text("one\ntwo\n", encoding="utf-8")
    lines, offset = read_appended_lines(log, 0)
    assert lines == ["one", "two"]

    with log.open("a", encoding="utf-8") as handle:
        handle.write("three\n")
    new_lines, _ = read_appended_lines(log, offset)
    assert new_lines == ["three"]


def test_logs_follow_streams_appended_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "dataagent.log"
    log.write_text("first\n", encoding="utf-8")
    stop = threading.Event()
    thread = threading.Thread(
        target=follow_logs,
        args=(log, stop),
        kwargs={"poll_interval_s": 0.02},
        daemon=True,
    )
    thread.start()
    try:
        time.sleep(0.15)
        with log.open("a", encoding="utf-8") as handle:
            handle.write("second\n")
        time.sleep(0.3)
    finally:
        stop.set()
        thread.join(timeout=2)

    out = capsys.readouterr().out
    assert "first" in out
    assert "second" in out


def test_status_prints_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    for label in ("任务", "队列深度", "存活 worker"):
        assert label in out


def test_bench_prints_baseline_metrics(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bench"]) == 0
    out = capsys.readouterr().out
    assert "p50" in out
    assert "p95" in out


def test_up_prints_url_and_serves_health(capsys: pytest.CaptureFixture[str]) -> None:
    """up：自检 → 启动 API → 打印 http://127.0.0.1:<port>（CLI 唯一入口）。"""
    service = start_service(host="127.0.0.1", port=0, role="api")
    try:
        out = capsys.readouterr().out
        assert service.url in out
        with urllib.request.urlopen(f"{service.url}/health", timeout=5) as response:
            assert response.status == 200
    finally:
        service.stop()
