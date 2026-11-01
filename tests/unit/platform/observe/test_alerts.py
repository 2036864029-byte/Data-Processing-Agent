"""告警测试（T063，A9）。

异常触发（STP 跌破 / 换引擎占比上涨 / 队列积压 / 节点离线 / 失败分布漂移 /
plugin_reason_unknown >5% 等）；告警为旁路、不阻塞主链路。
"""

import json
import threading
import time

import pytest

from dataagent.kernel.state import State
from dataagent.platform.observe.alerts import (
    Alert,
    AlertEvaluator,
    AlertKind,
    AlertSeverity,
    AlertThresholds,
    AsyncAlertDispatcher,
)

HEALTHY_STATS: dict[str, float] = {
    "stp_rate": 0.85,
    "misdrop_rate": 0.01,
    "cost_growth_ratio": 0.05,
    "engine_switch_ratio": 0.10,
    "queue_depth": 10,
    "node_heartbeat_age_s": 5.0,
    "failure_distribution_drift": 0.05,
    "plugin_reason_unknown_ratio": 0.01,
}


def make_alert(kind: AlertKind = AlertKind.STP_BELOW_THRESHOLD) -> Alert:
    return Alert(
        kind=kind,
        severity=AlertSeverity.WARNING,
        message="test",
        metrics={"value": 1},
    )


def test_alert_kinds_cover_required_triggers() -> None:
    assert {kind.value for kind in AlertKind} == {
        "stp_below_threshold",
        "misdrop_rate_exceeded",
        "cost_spike",
        "engine_switch_spike",
        "queue_backlog",
        "node_offline",
        "failure_distribution_drift",
        "plugin_reason_unknown_ratio",
        "unclassified_format",
    }


def test_healthy_stats_produce_no_alerts() -> None:
    assert AlertEvaluator().evaluate(HEALTHY_STATS) == ()


@pytest.mark.parametrize(
    ("stats", "kind"),
    [
        ({"stp_rate": 0.70}, AlertKind.STP_BELOW_THRESHOLD),
        ({"misdrop_rate": 0.05}, AlertKind.MISDROP_RATE_EXCEEDED),
        ({"cost_growth_ratio": 0.50}, AlertKind.COST_SPIKE),
        ({"engine_switch_ratio": 0.40}, AlertKind.ENGINE_SWITCH_SPIKE),
        ({"queue_depth": 5000}, AlertKind.QUEUE_BACKLOG),
        ({"node_heartbeat_age_s": 120.0}, AlertKind.NODE_OFFLINE),
        ({"failure_distribution_drift": 0.40}, AlertKind.FAILURE_DISTRIBUTION_DRIFT),
        ({"plugin_reason_unknown_ratio": 0.06}, AlertKind.PLUGIN_REASON_UNKNOWN_RATIO),
        ({"unclassified_format_count": 1}, AlertKind.UNCLASSIFIED_FORMAT),
    ],
)
def test_each_alert_trigger(stats: dict[str, float], kind: AlertKind) -> None:
    alerts = AlertEvaluator().evaluate(stats)
    assert [alert.kind for alert in alerts] == [kind]
    assert alerts[0].metrics


def test_evaluator_is_deterministic_and_serializable() -> None:
    stats = {**HEALTHY_STATS, "stp_rate": 0.50, "queue_depth": 5000}
    first = AlertEvaluator().evaluate(stats)
    second = AlertEvaluator().evaluate(stats)

    assert first == second
    assert {alert.kind for alert in first} == {
        AlertKind.STP_BELOW_THRESHOLD,
        AlertKind.QUEUE_BACKLOG,
    }
    assert json.dumps([alert.as_dict() for alert in first])


def test_custom_thresholds() -> None:
    evaluator = AlertEvaluator(AlertThresholds(stp_min=0.95))
    assert evaluator.evaluate({"stp_rate": 0.90})[0].kind is AlertKind.STP_BELOW_THRESHOLD


class BlockingSink:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.received: list[Alert] = []

    def emit(self, alert: Alert) -> None:
        self.started.set()
        self.release.wait(timeout=5)
        self.received.append(alert)


def test_dispatcher_does_not_block_main_path() -> None:
    sink = BlockingSink()
    dispatcher = AsyncAlertDispatcher(sink, max_pending=4)
    started = time.perf_counter()
    try:
        assert dispatcher.emit(make_alert()) is True
        elapsed = time.perf_counter() - started
        assert elapsed < 0.5
        assert sink.started.wait(timeout=2)
    finally:
        sink.release.set()
        dispatcher.close()
    assert len(sink.received) == 1


def test_dispatcher_drops_when_backlog_full() -> None:
    sink = BlockingSink()
    dispatcher = AsyncAlertDispatcher(sink, max_pending=1)
    try:
        assert dispatcher.emit(make_alert()) is True
        assert sink.started.wait(timeout=2)
        assert dispatcher.emit(make_alert()) is True  # 占满待发送队列
        assert dispatcher.emit(make_alert()) is False  # 旁路丢弃，不阻塞
        assert dispatcher.dropped == 1
    finally:
        sink.release.set()
        dispatcher.close()


class FailOnceSink:
    def __init__(self) -> None:
        self.calls = 0
        self.received: list[Alert] = []

    def emit(self, alert: Alert) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("sink down")
        self.received.append(alert)


def test_sink_failure_does_not_stop_dispatcher() -> None:
    sink = FailOnceSink()
    dispatcher = AsyncAlertDispatcher(sink, max_pending=10)
    try:
        dispatcher.emit(make_alert(AlertKind.QUEUE_BACKLOG))
        dispatcher.emit(make_alert(AlertKind.NODE_OFFLINE))
        deadline = time.time() + 2
        while sink.calls < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert sink.calls == 2
        assert [alert.kind for alert in sink.received] == [AlertKind.NODE_OFFLINE]
        assert dispatcher.failures == 1
    finally:
        dispatcher.close()


class _RecordingSink:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def emit(self, alert: Alert) -> None:
        self.alerts.append(alert)


class _Repository:
    def state(self, task_id: str) -> State:
        return State.PARSING

    def save_state(self, task_id: str, state: State) -> None:
        return None

    def resume_point(self, task_id: str) -> int | None:
        return None

    def commit_batch(self, task_id: str, batch_index: int) -> None:
        return None


class _Executor:
    def execute(self, request: object) -> object:
        from dataagent.kernel.supervisor import BatchOutcome

        return BatchOutcome(artifact_ids=())


class _Artifacts:
    def write(self, artifact_id: str) -> None:
        return None


def test_supervisor_exposes_alert_trigger_point() -> None:
    """告警触发点接入 supervisor（报告生成处后续复用同一 evaluator）。"""
    from dataagent.kernel.supervisor import Supervisor

    sink = _RecordingSink()
    supervisor = Supervisor(
        executor=_Executor(),  # type: ignore[arg-type]
        artifacts=_Artifacts(),
        repository=_Repository(),
        alerts=AlertEvaluator(),
        alert_sink=sink,
    )

    alerts = supervisor.evaluate_alerts({"stp_rate": 0.5})

    assert [alert.kind for alert in alerts] == [AlertKind.STP_BELOW_THRESHOLD]
    assert sink.alerts == list(alerts)
