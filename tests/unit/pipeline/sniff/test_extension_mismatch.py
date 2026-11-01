"""后缀不符与无法分类测试（T079 / T082，B2）。

后缀只作提示：CSV 改名 `.pdf` 按内容判定并记录 `extension_mismatch` 事件；
无法分类进待确认队列并告警，不得静默丢弃；歧义走小样本双跑决策。
"""

import io
import json
import logging
from pathlib import Path

from dataagent.pipeline.ingest.ingestor import InMemoryPendingQueue
from dataagent.pipeline.sniff.dispatcher import (
    DispatchAction,
    DualRunSample,
    FormatDispatcher,
    decide_ambiguous,
)
from dataagent.pipeline.sniff.sniffer import Family, sniff_file
from dataagent.platform.observe.alerts import Alert, AlertEvaluator, AlertKind
from dataagent.platform.observe.log import configure_logging

CSV = "a,b,c\n1,2,3\n4,5,6\n7,8,9\n"
WEAK = "a,b,c\n1,2,3\n4,5,6\n7,8\n9,10\n"


class RecordingSink:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def emit(self, alert: Alert) -> None:
        self.alerts.append(alert)


class FakeDualRunner:
    def __init__(self, samples: dict[Family, DualRunSample]) -> None:
        self._samples = samples
        self.calls: list[tuple[Family, int]] = []

    def run(self, path: Path, family: Family, *, sample_lines: int = 50) -> DualRunSample:
        self.calls.append((family, sample_lines))
        return self._samples[family]


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_renamed_csv_is_tabular_and_records_extension_mismatch(
    tmp_path: Path,
) -> None:
    stream = io.StringIO()
    configure_logging(stream=stream, level=logging.INFO)
    path = write(tmp_path, "renamed.pdf", CSV.encode())

    result = sniff_file(path)

    assert result.family is Family.TABULAR
    assert result.extension_mismatch is True
    events = [
        json.loads(line) for line in stream.getvalue().splitlines() if line.strip()
    ]
    mismatch = [event for event in events if event["event"] == "extension_mismatch"]
    assert mismatch
    assert mismatch[0]["declared"] == "pdf"
    assert mismatch[0]["detected"] == "tabular"


def test_matching_extension_has_no_mismatch(tmp_path: Path) -> None:
    result = sniff_file(write(tmp_path, "data.csv", CSV.encode()))
    assert result.extension_mismatch is False


def test_unknown_extension_with_text_is_text_without_mismatch(
    tmp_path: Path,
) -> None:
    result = sniff_file(write(tmp_path, "data.dat", b"hello\nworld\n"))
    assert result.family is Family.TEXT
    assert result.extension_mismatch is False


def test_unclassified_goes_to_pending_queue_and_alerts(tmp_path: Path) -> None:
    pending = InMemoryPendingQueue()
    sink = RecordingSink()
    dispatcher = FormatDispatcher(pending=pending, alerts=AlertEvaluator(), alert_sink=sink)
    path = write(tmp_path, "blob.bin", bytes(range(256)) * 4)
    result = sniff_file(path)

    outcome = dispatcher.dispatch("sha256:blob", path, result)

    assert outcome.action is DispatchAction.PENDING
    records = pending.all()
    assert len(records) == 1
    assert records[0].reason == "unclassified_format"
    assert records[0].diagnostics["family"] == "unknown"
    assert [alert.kind for alert in sink.alerts] == [AlertKind.UNCLASSIFIED_FORMAT]


def test_ambiguous_runs_dual_sample_and_chooses_by_quality_then_cost(
    tmp_path: Path,
) -> None:
    path = write(tmp_path, "weak.csv", WEAK.encode())
    result = sniff_file(path)
    assert result.state.value == "ambiguous"

    runner = FakeDualRunner(
        {
            Family.TABULAR: DualRunSample(Family.TABULAR, quality=0.92, cost=3.0),
            Family.TEXT: DualRunSample(Family.TEXT, quality=0.90, cost=1.0),
        }
    )
    decision = decide_ambiguous(path, result.candidates, runner)

    # 质量差距 < 0.05 → 选成本更低者
    assert decision.chosen is Family.TEXT
    assert {family for family, _ in runner.calls} == {Family.TABULAR, Family.TEXT}
    assert all(sample_lines == 50 for _, sample_lines in runner.calls)


def test_dual_run_prefers_clearly_better_quality() -> None:
    path = Path("ignored")
    runner = FakeDualRunner(
        {
            Family.TABULAR: DualRunSample(Family.TABULAR, quality=0.95, cost=5.0),
            Family.TEXT: DualRunSample(Family.TEXT, quality=0.60, cost=1.0),
        }
    )
    decision = decide_ambiguous(path, (Family.TABULAR, Family.TEXT), runner)
    assert decision.chosen is Family.TABULAR


def test_dispatcher_uses_dual_run_decision(tmp_path: Path) -> None:
    pending = InMemoryPendingQueue()
    sink = RecordingSink()
    dispatcher = FormatDispatcher(pending=pending, alerts=AlertEvaluator(), alert_sink=sink)
    path = write(tmp_path, "weak.csv", WEAK.encode())
    result = sniff_file(path)
    runner = FakeDualRunner(
        {
            Family.TABULAR: DualRunSample(Family.TABULAR, quality=0.90, cost=1.0),
            Family.TEXT: DualRunSample(Family.TEXT, quality=0.70, cost=1.0),
        }
    )

    outcome = dispatcher.dispatch("sha256:weak", path, result, runner=runner)

    assert outcome.action is DispatchAction.DUAL_RUN
    assert outcome.family is Family.TABULAR
    assert pending.all() == []
