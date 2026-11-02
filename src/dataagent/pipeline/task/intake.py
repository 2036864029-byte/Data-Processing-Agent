"""任务接入阶段：体检 / 去重 / 格式判定与分派 / 两级路由 / 画像路由段。

只负责“把文件变成 DocumentContext”，不执行抽取、质检或交付。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dataagent.pipeline.ingest.ingestor import (
    Ingestor,
    IngestOutcome,
)
from dataagent.pipeline.profile.writer import PageProfileWriter
from dataagent.pipeline.route.routes import TwoLevelRouter
from dataagent.pipeline.sniff.dispatcher import DispatchAction, FormatDispatcher
from dataagent.pipeline.sniff.sniffer import Family, sniff_file
from dataagent.pipeline.task.context import DocumentContext
from dataagent.pipeline.task.results import DocumentOutcome
from dataagent.pipeline.telemetry import TaskTelemetry


@dataclass
class IntakeBatch:
    contexts: list[DocumentContext] = field(default_factory=list)
    outcomes: list[DocumentOutcome] = field(default_factory=list)
    duplicates: list[tuple[str, str]] = field(default_factory=list)


class IntakeService:
    def __init__(
        self,
        *,
        ingestor: Ingestor,
        dispatcher: FormatDispatcher,
        router: TwoLevelRouter,
        profiles: PageProfileWriter,
        telemetry: TaskTelemetry,
    ) -> None:
        self._ingestor = ingestor
        self._dispatcher = dispatcher
        self._router = router
        self._profiles = profiles
        self._telemetry = telemetry

    def analyze_all(self, paths: Sequence[Path]) -> IntakeBatch:
        batch = IntakeBatch()
        in_flight: dict[str, DocumentContext] = {}
        for path in paths:
            result = self.analyze(path, in_flight=in_flight)
            if isinstance(result, DocumentOutcome):
                batch.outcomes.append(result)
                if result.status == "duplicate":
                    batch.duplicates.append((result.path, result.doc_id))
                continue
            in_flight[result.record.doc_id] = result
            batch.contexts.append(result)
        return batch

    def analyze(
        self,
        path: Path,
        *,
        in_flight: Mapping[str, DocumentContext] | None = None,
    ) -> DocumentContext | DocumentOutcome:
        ingested = self._ingestor.ingest_path(path)
        if ingested.outcome is IngestOutcome.REJECTED:
            self._audit(
                ingested.document.doc_id,
                ingested.document.path,
                status="rejected",
                family="unknown",
            )
            return DocumentOutcome(
                doc_id=ingested.document.doc_id,
                path=ingested.document.path,
                family="unknown",
                status="rejected",
                note="体检拦截，已进待确认队列",
            )

        if ingested.outcome is IngestOutcome.DUPLICATE:
            return self._duplicate_outcome(path, ingested.document.doc_id, in_flight or {})

        record = ingested.document
        document_path = Path(record.path)
        sniff = sniff_file(document_path)
        dispatch = self._dispatcher.dispatch(record.doc_id, document_path, sniff)
        if dispatch.action is not DispatchAction.ROUTE:
            reason = dispatch.pending.reason if dispatch.pending is not None else "pending"
            self._audit(
                record.doc_id,
                str(document_path),
                status="pending",
                family=dispatch.family.value,
            )
            return DocumentOutcome(
                doc_id=record.doc_id,
                path=str(document_path),
                family=dispatch.family.value,
                status="pending",
                note=reason,
            )

        routing = self._router.route(doc_id=record.doc_id, path=document_path, sniff=sniff)
        if routing.family is Family.PDF:
            for decision in routing.page_decisions:
                self._profiles.write_route(decision)
            page_count = (
                routing.classification.page_count if routing.classification is not None else 0
            )
            universe = tuple(range(1, page_count + 1))
        else:
            universe = (1,)

        context = DocumentContext(
            record=record,
            path=document_path,
            sniff=sniff,
            routing=routing,
            universe=universe,
        )
        self._audit(
            record.doc_id,
            str(document_path),
            status=ingested.outcome.value,
            family=routing.family.value,
        )
        return context

    def _duplicate_outcome(
        self,
        path: Path,
        doc_id: str,
        in_flight: Mapping[str, DocumentContext],
    ) -> DocumentOutcome:
        existing = in_flight.get(doc_id)
        if existing is not None:
            self._audit(
                doc_id,
                str(path),
                status="duplicate",
                family=existing.routing.family.value,
            )
            return DocumentOutcome(
                doc_id=doc_id,
                path=str(path),
                family=existing.routing.family.value,
                status="duplicate",
                note="内容哈希重复，复用已有结果",
            )
        self._audit(doc_id, str(path), status="duplicate", family="unknown")
        return DocumentOutcome(
            doc_id=doc_id,
            path=str(path),
            family="unknown",
            status="duplicate",
            note="内容哈希重复，已有记录（本次未处理）",
        )

    def _audit(self, doc_id: str, path: str, *, status: str, family: str) -> None:
        self._telemetry.record_ingest(doc_id=doc_id, path=path, status=status, family=family)
