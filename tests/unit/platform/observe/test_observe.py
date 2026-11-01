"""观测基础测试（T062，A9）。

结构化日志 + span 上下文；FR-025 指标清单逐项断言采集点与口径；审计留痕可追溯。
"""

import io
import json
import logging
from datetime import UTC, datetime

import pytest

from dataagent.contracts.policies import Tier
from dataagent.platform.observe.audit import (
    AuditAction,
    Auditor,
    InMemoryAuditSink,
)
from dataagent.platform.observe.log import (
    TRACE_FIELDS,
    configure_logging,
    current_span,
    log_event,
    span,
)
from dataagent.platform.observe.metrics import (
    METRIC_SPECS,
    LoggingMetricsSink,
    MetricsCollector,
    percentile,
)

EXPECTED_METRICS = {
    "processed_pages",
    "processed_docs",
    "stp_rate",
    "misdrop_rate",
    "cost_per_page",
    "tier_distribution",
    "postprocess_hit_rate",
    "latency_p50_ms",
    "latency_p95_ms",
    "complex_page_ratio",
}


def capture_logs() -> io.StringIO:
    stream = io.StringIO()
    configure_logging(stream=stream, level=logging.INFO)
    return stream


def test_structured_log_contains_span_context() -> None:
    stream = capture_logs()
    with span("parse", task_id="task-1", doc_id="sha256:doc"):
        log_event("page_done", page_no=1, duration_ms=12.5)

    payload = json.loads(stream.getvalue().strip())
    assert payload["event"] == "page_done"
    assert payload["level"] == "INFO"
    assert payload["span"] == "parse"
    assert payload["task_id"] == "task-1"
    assert payload["doc_id"] == "sha256:doc"
    assert payload["page_no"] == 1
    assert payload["duration_ms"] == 12.5
    assert len(payload["trace_id"]) > 0
    assert len(payload["span_id"]) > 0


def test_nested_spans_share_trace_and_link_parent() -> None:
    with span("task", task_id="task-1") as outer:
        assert current_span() is outer
        with span("batch", page_range="1-32") as inner:
            assert inner.trace_id == outer.trace_id
            assert inner.parent_span_id == outer.span_id
            assert inner.fields["page_range"] == "1-32"
    assert current_span() is None


def test_span_accepts_explicit_trace_id() -> None:
    with span("task", trace_id="trace-fixed") as ctx:
        assert ctx.trace_id == "trace-fixed"


def test_log_outside_span_has_no_trace_fields() -> None:
    stream = capture_logs()
    log_event("startup")
    payload = json.loads(stream.getvalue().strip())
    assert "trace_id" not in payload


def test_trace_fields_cover_prd_contract() -> None:
    """Trace 字段要求（PRD 4.9.1）逐项保留。"""
    assert set(TRACE_FIELDS) == {
        "task_id",
        "doc_id",
        "page_no",
        "stage",
        "engine",
        "params_hash",
        "duration_ms",
        "cost",
        "verdict",
        "tier",
        "attribution",
        "action",
    }


def test_metric_specs_cover_fr025_list_with_definitions() -> None:
    assert {name.value for name in METRIC_SPECS} == EXPECTED_METRICS
    for name, spec in METRIC_SPECS.items():
        assert spec.name is name
        assert spec.definition.strip()
        assert spec.unit.strip()


def make_collector() -> MetricsCollector:
    collector = MetricsCollector()
    collector.record_document(stp=True)
    collector.record_document(stp=False)
    collector.record_page(tier=Tier.T0, cost=0.0, latency_ms=10.0)
    collector.record_page(tier=Tier.T1, cost=0.1, latency_ms=20.0)
    collector.record_page(tier=Tier.T2, cost=0.2, latency_ms=30.0, is_complex=True)
    collector.record_page(tier=Tier.T3, cost=0.3, latency_ms=40.0)
    collector.record_postprocess(hit=True)
    collector.record_postprocess(hit=False)
    collector.record_review(system_pass=True, human_pass=False)
    collector.record_review(system_pass=False, human_pass=False)
    collector.record_review(system_pass=True, human_pass=True)
    return collector


def test_metrics_are_computed_with_prd_definitions() -> None:
    snapshot = make_collector().snapshot()

    assert snapshot["processed_docs"] == 2
    assert snapshot["processed_pages"] == 4
    assert snapshot["stp_rate"] == 0.5  # 一次通过文档数 / 总文档数
    assert snapshot["misdrop_rate"] == 0.5  # 人工不合格样本中系统判 pass 的比例
    assert snapshot["cost_per_page"] == pytest.approx(0.15)  # 总成本 / 总页数
    assert snapshot["tier_distribution"] == {
        "T0": 0.25,
        "T1": 0.25,
        "T2": 0.25,
        "T3": 0.25,
    }
    assert snapshot["postprocess_hit_rate"] == 0.5  # T1 修复成功页 / 走 T1 页
    assert snapshot["latency_p50_ms"] == 20.0
    assert snapshot["latency_p95_ms"] == 40.0
    assert snapshot["complex_page_ratio"] == 0.25


def test_metrics_zero_division_is_safe() -> None:
    snapshot = MetricsCollector().snapshot()
    assert snapshot["stp_rate"] == 0.0
    assert snapshot["misdrop_rate"] == 0.0
    assert snapshot["cost_per_page"] == 0.0
    assert snapshot["postprocess_hit_rate"] == 0.0
    assert snapshot["complex_page_ratio"] == 0.0
    assert snapshot["latency_p50_ms"] == 0.0
    assert snapshot["latency_p95_ms"] == 0.0
    assert snapshot["tier_distribution"] == {"T0": 0.0, "T1": 0.0, "T2": 0.0, "T3": 0.0}


def test_percentile_is_deterministic() -> None:
    values = [40.0, 10.0, 30.0, 20.0]
    assert percentile(values, 50) == 20.0
    assert percentile(values, 95) == 40.0
    assert percentile([], 95) == 0.0


def test_logging_metrics_sink_emits_snapshot() -> None:
    stream = capture_logs()
    LoggingMetricsSink().emit("task-1", {"stp_rate": 0.5, "processed_pages": 4})
    payload = json.loads(stream.getvalue().strip())
    assert payload["event"] == "metrics_snapshot"
    assert payload["task_id"] == "task-1"
    assert payload["stp_rate"] == 0.5
    assert payload["processed_pages"] == 4


def test_audit_records_all_four_action_types() -> None:
    sink = InMemoryAuditSink()
    clock = lambda: datetime(2026, 11, 1, tzinfo=UTC)  # noqa: E731
    auditor = Auditor(sink, clock=clock)

    records = [
        auditor.record(action, actor="ops", target="config.yaml", note=action.value)
        for action in AuditAction
    ]

    assert sink.records == records
    assert [record.action for record in records] == list(AuditAction)
    assert records[0].occurred_at == "2026-11-01T00:00:00+00:00"
    assert json.dumps(records[0].as_dict())


def test_audit_captures_trace_id_from_span() -> None:
    sink = InMemoryAuditSink()
    auditor = Auditor(sink)
    with span("task", task_id="task-1") as ctx:
        record = auditor.record(
            AuditAction.MANUAL_FIX,
            actor="human",
            target="page:3",
            defect="table_structure_broken",
        )
    assert record.trace_id == ctx.trace_id
    assert record.as_dict()["detail"]["defect"] == "table_structure_broken"

    outside = auditor.record(AuditAction.INGEST, actor="system", target="folder")
    assert outside.trace_id is None
