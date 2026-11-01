"""一级判定结果分派：歧义小样本双跑 / 无法分类进待确认队列并告警（T082）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Protocol

from dataagent.pipeline.ingest.ingestor import PendingQueue, PendingRecord
from dataagent.pipeline.sniff.sniffer import AMBIGUOUS_LINES, Family, SniffResult, SniffState
from dataagent.platform.observe.alerts import AlertEvaluator, AlertSink

QUALITY_TIE_BAND = 0.05


@unique
class DispatchAction(StrEnum):
    ROUTE = "route"
    DUAL_RUN = "dual_run"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class DualRunSample:
    family: Family
    quality: float
    cost: float


@dataclass(frozen=True, slots=True)
class DualRunDecision:
    chosen: Family
    samples: tuple[DualRunSample, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    action: DispatchAction
    family: Family
    sniff: SniffResult
    dual_run: DualRunDecision | None = None
    pending: PendingRecord | None = None


class DualRunner(Protocol):
    def run(
        self, path: Path, family: Family, *, sample_lines: int = AMBIGUOUS_LINES
    ) -> DualRunSample: ...


def decide_ambiguous(
    path: Path, candidates: tuple[Family, ...], runner: DualRunner
) -> DualRunDecision:
    """歧义不得直接猜：两个候选各跑前 50 行小样本，按质量/成本决策。

    规则：质量领先 ≥0.05 的候选优先；否则取成本更低者；再平手按候选顺序。
    """
    samples = tuple(
        runner.run(path, family, sample_lines=AMBIGUOUS_LINES)
        for family in candidates
    )
    best_quality = max(sample.quality for sample in samples)
    finalists = [
        sample for sample in samples if best_quality - sample.quality < QUALITY_TIE_BAND
    ]
    chosen = min(
        finalists, key=lambda sample: (sample.cost, candidates.index(sample.family))
    )
    rationale = (
        f"quality_leader={best_quality:.2f}; "
        f"chosen={chosen.family.value} (quality={chosen.quality:.2f}, cost={chosen.cost:g})"
    )
    return DualRunDecision(chosen=chosen.family, samples=samples, rationale=rationale)


class FormatDispatcher:
    def __init__(
        self,
        *,
        pending: PendingQueue,
        alerts: AlertEvaluator | None = None,
        alert_sink: AlertSink | None = None,
    ) -> None:
        self._pending = pending
        self._alerts = alerts
        self._alert_sink = alert_sink

    def dispatch(
        self,
        doc_id: str,
        path: Path | str,
        sniff: SniffResult,
        *,
        runner: DualRunner | None = None,
    ) -> DispatchOutcome:
        if sniff.state is SniffState.UNCLASSIFIED:
            return self._to_pending(doc_id, path, sniff, reason="unclassified_format")

        if sniff.state is SniffState.AMBIGUOUS:
            if runner is None:
                return self._to_pending(
                    doc_id, path, sniff, reason="ambiguous_without_dual_run"
                )
            decision = decide_ambiguous(Path(path), sniff.candidates, runner)
            return DispatchOutcome(
                action=DispatchAction.DUAL_RUN,
                family=decision.chosen,
                sniff=sniff,
                dual_run=decision,
            )

        return DispatchOutcome(action=DispatchAction.ROUTE, family=sniff.family, sniff=sniff)

    def _to_pending(
        self, doc_id: str, path: Path | str, sniff: SniffResult, *, reason: str
    ) -> DispatchOutcome:
        record = PendingRecord(
            doc_id=doc_id,
            source="sniff",
            path=str(path),
            reason=reason,
            diagnostics=sniff.as_dict(),
        )
        self._pending.enqueue(record)
        if self._alerts is not None:
            for alert in self._alerts.evaluate({"unclassified_format_count": 1}):
                if self._alert_sink is not None:
                    self._alert_sink.emit(alert)
        return DispatchOutcome(
            action=DispatchAction.PENDING,
            family=sniff.family,
            sniff=sniff,
            pending=record,
        )
