"""告警规则与旁路分发（FR-025 / PRD 4.9.3）。

规则为纯函数（同一统计快照同一结果）；分发走独立线程 + 有界队列，
队列满直接丢弃并计数——**告警绝不能阻塞主链路**。
"""

from __future__ import annotations

import queue as queue_module
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Any, Final, Protocol


@unique
class AlertKind(StrEnum):
    STP_BELOW_THRESHOLD = "stp_below_threshold"
    MISDROP_RATE_EXCEEDED = "misdrop_rate_exceeded"
    COST_SPIKE = "cost_spike"
    ENGINE_SWITCH_SPIKE = "engine_switch_spike"
    QUEUE_BACKLOG = "queue_backlog"
    NODE_OFFLINE = "node_offline"
    FAILURE_DISTRIBUTION_DRIFT = "failure_distribution_drift"
    PLUGIN_REASON_UNKNOWN_RATIO = "plugin_reason_unknown_ratio"


@unique
class AlertSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class AlertThresholds:
    stp_min: float = 0.80
    misdrop_max: float = 0.02
    cost_growth_ratio_max: float = 0.20
    engine_switch_ratio_max: float = 0.20
    queue_depth_max: int = 1000
    node_offline_age_s: float = 60.0
    failure_drift_max: float = 0.20
    plugin_reason_unknown_ratio_max: float = 0.05


DEFAULT_THRESHOLDS: Final[AlertThresholds] = AlertThresholds()


@dataclass(frozen=True, slots=True)
class Alert:
    kind: AlertKind
    severity: AlertSeverity
    message: str
    metrics: Mapping[str, float | int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "severity": self.severity.value,
            "message": self.message,
            "metrics": dict(self.metrics),
        }


class AlertEvaluator:
    """按统计快照评估告警；缺省的统计键不触发（尚未采集）。"""

    def __init__(self, thresholds: AlertThresholds = DEFAULT_THRESHOLDS) -> None:
        self._thresholds = thresholds

    def evaluate(self, stats: Mapping[str, float | int]) -> tuple[Alert, ...]:
        thresholds = self._thresholds
        alerts: list[Alert] = []

        stp_rate = _number(stats, "stp_rate")
        if stp_rate is not None and stp_rate < thresholds.stp_min:
            alerts.append(
                _alert(
                    AlertKind.STP_BELOW_THRESHOLD,
                    AlertSeverity.CRITICAL,
                    f"STP 率 {stp_rate:.2%} 低于阈值 {thresholds.stp_min:.2%}",
                    stp_rate,
                    thresholds.stp_min,
                )
            )

        misdrop_rate = _number(stats, "misdrop_rate")
        if misdrop_rate is not None and misdrop_rate > thresholds.misdrop_max:
            alerts.append(
                _alert(
                    AlertKind.MISDROP_RATE_EXCEEDED,
                    AlertSeverity.CRITICAL,
                    f"误放率 {misdrop_rate:.2%} 超过阈值 {thresholds.misdrop_max:.2%}",
                    misdrop_rate,
                    thresholds.misdrop_max,
                )
            )

        cost_growth = _number(stats, "cost_growth_ratio")
        if cost_growth is not None and cost_growth > thresholds.cost_growth_ratio_max:
            alerts.append(
                _alert(
                    AlertKind.COST_SPIKE,
                    AlertSeverity.WARNING,
                    f"单页成本上涨 {cost_growth:.2%} 超过阈值 "
                    f"{thresholds.cost_growth_ratio_max:.2%}",
                    cost_growth,
                    thresholds.cost_growth_ratio_max,
                )
            )

        engine_switch = _number(stats, "engine_switch_ratio")
        if (
            engine_switch is not None
            and engine_switch > thresholds.engine_switch_ratio_max
        ):
            alerts.append(
                _alert(
                    AlertKind.ENGINE_SWITCH_SPIKE,
                    AlertSeverity.WARNING,
                    f"换引擎（T3）占比 {engine_switch:.2%} 超过阈值 "
                    f"{thresholds.engine_switch_ratio_max:.2%}",
                    engine_switch,
                    thresholds.engine_switch_ratio_max,
                )
            )

        queue_depth = _number(stats, "queue_depth")
        if queue_depth is not None and queue_depth > thresholds.queue_depth_max:
            alerts.append(
                _alert(
                    AlertKind.QUEUE_BACKLOG,
                    AlertSeverity.WARNING,
                    f"队列积压 {int(queue_depth)} 超过阈值 {thresholds.queue_depth_max}",
                    int(queue_depth),
                    thresholds.queue_depth_max,
                )
            )

        heartbeat_age = _number(stats, "node_heartbeat_age_s")
        if heartbeat_age is not None and heartbeat_age > thresholds.node_offline_age_s:
            alerts.append(
                _alert(
                    AlertKind.NODE_OFFLINE,
                    AlertSeverity.CRITICAL,
                    f"节点 {heartbeat_age:g}s 无心跳，超过阈值 "
                    f"{thresholds.node_offline_age_s:g}s",
                    heartbeat_age,
                    thresholds.node_offline_age_s,
                )
            )

        drift = _number(stats, "failure_distribution_drift")
        if drift is not None and drift > thresholds.failure_drift_max:
            alerts.append(
                _alert(
                    AlertKind.FAILURE_DISTRIBUTION_DRIFT,
                    AlertSeverity.WARNING,
                    f"失败分布漂移 {drift:.2%} 超过阈值 {thresholds.failure_drift_max:.2%}",
                    drift,
                    thresholds.failure_drift_max,
                )
            )

        unknown_ratio = _number(stats, "plugin_reason_unknown_ratio")
        if (
            unknown_ratio is not None
            and unknown_ratio > thresholds.plugin_reason_unknown_ratio_max
        ):
            alerts.append(
                _alert(
                    AlertKind.PLUGIN_REASON_UNKNOWN_RATIO,
                    AlertSeverity.WARNING,
                    f"plugin_reason_unknown 占比 {unknown_ratio:.2%} 超过阈值 "
                    f"{thresholds.plugin_reason_unknown_ratio_max:.2%}"
                    "（疑似插件升级或映射表过期）",
                    unknown_ratio,
                    thresholds.plugin_reason_unknown_ratio_max,
                )
            )

        return tuple(alerts)


def _number(stats: Mapping[str, float | int], key: str) -> float | None:
    value = stats.get(key)
    return None if value is None else float(value)


def _alert(
    kind: AlertKind,
    severity: AlertSeverity,
    message: str,
    observed: float | int,
    threshold: float | int,
) -> Alert:
    return Alert(
        kind=kind,
        severity=severity,
        message=message,
        metrics={"observed": observed, "threshold": threshold},
    )


class AlertSink(Protocol):
    def emit(self, alert: Alert) -> None: ...


class AsyncAlertDispatcher:
    """告警旁路：独立线程发送；队列满丢弃并计数，`emit` 永不阻塞主链路。"""

    def __init__(self, sink: AlertSink, *, max_pending: int = 1024) -> None:
        if max_pending < 1:
            raise ValueError("max_pending 必须 ≥ 1")
        self._sink = sink
        self._queue: queue_module.Queue[Alert | None] = queue_module.Queue(
            maxsize=max_pending
        )
        self.dropped = 0
        self.failures = 0
        self._thread = threading.Thread(
            target=self._drain, name="dataagent-alerts", daemon=True
        )
        self._thread.start()

    def emit(self, alert: Alert) -> bool:
        try:
            self._queue.put_nowait(alert)
            return True
        except queue_module.Full:
            self.dropped += 1
            return False

    def close(self, *, timeout: float = 5.0) -> None:
        try:
            self._queue.put_nowait(None)
        except queue_module.Full:
            return
        self._thread.join(timeout=timeout)

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                self._sink.emit(item)
            except Exception:
                self.failures += 1
