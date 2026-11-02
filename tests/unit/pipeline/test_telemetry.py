"""全链路观测接线测试：span 日志、指标快照、接入审计、任务级告警。"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from dataagent.pipeline.task_runner import create_builtin_runner
from dataagent.pipeline.telemetry import TaskTelemetry
from dataagent.platform.observe.alerts import (
    Alert,
    AlertEvaluator,
    AlertKind,
    AlertThresholds,
)
from dataagent.platform.observe.audit import AuditAction, Auditor, InMemoryAuditSink
from dataagent.platform.observe.log import configure_logging

DOCS = [("dense", 60), ("sparse", 1)]


class RecordingMetrics:
    def __init__(self) -> None:
        self.snapshots: list[tuple[str, dict[str, Any]]] = []

    def emit(self, task_id: str, snapshot: Mapping[str, Any]) -> None:
        self.snapshots.append((task_id, dict(snapshot)))


class RecordingAlerts:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def emit(self, alert: Alert) -> None:
        self.alerts.append(alert)


def dense_lines(prefix: str, count: int) -> list[str]:
    return [
        f"{prefix} paragraph {index:03d} with supporting text"
        for index in range(count)
    ]


def build_telemetry() -> tuple[TaskTelemetry, RecordingMetrics, RecordingAlerts, InMemoryAuditSink]:
    metrics = RecordingMetrics()
    alerts = RecordingAlerts()
    audit = InMemoryAuditSink()
    telemetry = TaskTelemetry(
        metrics_sink=metrics,
        auditor=Auditor(audit),
        alerts=AlertEvaluator(AlertThresholds(stp_min=0.9)),
        alert_sink=alerts,
    )
    return telemetry, metrics, alerts, audit


def test_task_emits_metrics_audit_and_alerts(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    dense = write_pdf(tmp_path / "dense.pdf", [dense_lines("alpha", 60)])
    sparse = write_pdf(tmp_path / "sparse.pdf", [["hi"]])
    telemetry, metrics, alerts, audit = build_telemetry()
    runner = create_builtin_runner(
        output_dir=tmp_path / "out", telemetry=telemetry
    )

    outcome = runner.run([dense, sparse], task_id="obs-1")

    assert outcome.count("processed") == 2
    assert metrics.snapshots[0][0] == "obs-1"
    snapshot = metrics.snapshots[0][1]
    assert snapshot["processed_docs"] == 2
    assert snapshot["processed_pages"] == 2
    assert snapshot["stp_rate"] == 0.5
    assert set(snapshot["tier_distribution"]) == {"T0", "T1", "T2", "T3"}
    assert snapshot["latency_p95_ms"] >= 0.0

    ingest = [record for record in audit.records if record.action is AuditAction.INGEST]
    assert len(ingest) == 2
    assert all(record.trace_id for record in ingest)

    assert [alert.kind for alert in alerts.alerts] == [
        AlertKind.STP_BELOW_THRESHOLD
    ]


def test_process_pages_emits_metrics(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    sample = write_pdf(tmp_path / "one.pdf", [dense_lines("alpha", 60)])
    telemetry, metrics, alerts, _ = build_telemetry()
    runner = create_builtin_runner(
        output_dir=tmp_path / "out", telemetry=telemetry
    )

    runner.process_pages(sample, [1], task_id="obs-batch")

    assert metrics.snapshots[0][1]["processed_pages"] == 1
    assert metrics.snapshots[0][1]["processed_docs"] == 1
    assert alerts.alerts == []


def test_spans_produce_structured_logs(
    tmp_path: Path,
    write_pdf: Callable[[Path, list[list[str]]], Path],
) -> None:
    sample = write_pdf(tmp_path / "dense.pdf", [dense_lines("alpha", 60)])
    logger = logging.getLogger("dataagent")
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    previous_propagate = logger.propagate
    previous_disabled = logger.disabled
    buffer = io.StringIO()
    configure_logging(stream=buffer)
    try:
        runner = create_builtin_runner(output_dir=tmp_path / "out")
        runner.run([sample], task_id="obs-log")
    finally:
        logger.handlers.clear()
        logger.handlers.extend(previous_handlers)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
        logger.disabled = previous_disabled

    output = buffer.getvalue()
    for event in (
        "task_started",
        "document_finalized",
        "metrics_snapshot",
        "task_finished",
    ):
        assert event in output
    assert '"trace_id"' in output
    assert '"span": "task"' in output
