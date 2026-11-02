"""任务级观测接线：span + 指标 + 审计 + 告警（A9 能力接入 TaskRunner）。

- span：`log.span` 提供 trace/span 上下文，日志自动携带；
- 指标：`MetricsCollector` 累计页/文档/档位/时延，任务结束统一快照；
- 审计：接入动作写 `Auditor`（sink 可注入）；
- 告警：任务快照交 `AlertEvaluator`，告警走旁路 sink，绝不阻塞主链路。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from dataagent.contracts.policies import Tier
from dataagent.platform.observe.alerts import Alert, AlertEvaluator, AlertSink
from dataagent.platform.observe.audit import AuditAction, Auditor
from dataagent.platform.observe.log import log_event
from dataagent.platform.observe.metrics import (
    MetricsCollector,
    MetricsSink,
)


@dataclass
class TaskTelemetry:
    metrics: MetricsCollector = field(default_factory=MetricsCollector)
    metrics_sink: MetricsSink | None = None
    auditor: Auditor | None = None
    alerts: AlertEvaluator | None = None
    alert_sink: AlertSink | None = None

    def reset(self) -> None:
        """同一 runner 连续跑多个任务时清零累计指标。"""
        self.metrics = MetricsCollector()

    def record_ingest(
        self,
        *,
        doc_id: str,
        path: str,
        status: str,
        family: str,
    ) -> None:
        if self.auditor is None:
            return
        self.auditor.record(
            AuditAction.INGEST,
            actor="runner",
            target=doc_id,
            path=path,
            status=status,
            family=family,
        )

    def record_page(
        self,
        *,
        tier: Tier,
        latency_ms: float,
        cost: float = 0.0,
        is_complex: bool = False,
    ) -> None:
        self.metrics.record_page(
            tier=tier, cost=cost, latency_ms=latency_ms, is_complex=is_complex
        )

    def record_document(self, *, stp: bool) -> None:
        self.metrics.record_document(stp=stp)

    def record_postprocess(self, *, hit: bool) -> None:
        self.metrics.record_postprocess(hit=hit)

    def finish(
        self,
        task_id: str,
        *,
        extra: Mapping[str, float | int] | None = None,
    ) -> tuple[Alert, ...]:
        """任务结束：发指标快照 + 评估告警；返回触发的告警（便于测试）。"""
        snapshot = self.metrics.snapshot()
        if self.metrics_sink is not None:
            self.metrics_sink.emit(task_id, snapshot)
        else:
            log_event("metrics_snapshot", task_id=task_id, **snapshot)

        if self.alerts is None or snapshot.get("processed_docs", 0) == 0:
            return ()
        stats: dict[str, Any] = {**snapshot, **(extra or {})}
        triggered = self.alerts.evaluate(stats)
        if self.alert_sink is not None:
            for alert in triggered:
                self.alert_sink.emit(alert)
        return triggered
