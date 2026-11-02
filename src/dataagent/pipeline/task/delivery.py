"""文档交付服务：合并 / 分级交付 / 输出 / 报告 / 统计。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.merge.merger import merge_document
from dataagent.pipeline.output.delivery import DeliveryLevel, decide_delivery
from dataagent.pipeline.output.formats import to_csv, to_excel, to_json, to_markdown
from dataagent.pipeline.output.reports import DocSummary, PageDetail
from dataagent.pipeline.repair.fallback import FallbackPath, resolve_fallback
from dataagent.pipeline.repair.gap import PageGap
from dataagent.pipeline.task.context import DocumentContext
from dataagent.pipeline.task.models import DocumentOutcome, TaskConfig, TaskStats
from dataagent.pipeline.telemetry import TaskTelemetry
from dataagent.platform.observe.log import log_event

_SUFFIX = {"json": "json", "markdown": "md", "csv": "csv", "excel": "xlsx"}


def _gap_codes(gaps: Sequence[PageGap]) -> tuple[DefectCode, ...]:
    return tuple(dict.fromkeys(code for gap in gaps for code in gap.defect_codes))


def _failure_counts(
    context: DocumentContext,
) -> tuple[tuple[DefectCode, int], ...]:
    counts: dict[DefectCode, int] = {}
    for gap in context.gaps.values():
        for code in dict.fromkeys(gap.defect_codes):
            counts[code] = counts.get(code, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: item[0].value))


class DocumentDeliveryService:
    def __init__(
        self,
        *,
        output_dir: Path | str,
        config: TaskConfig,
        telemetry: TaskTelemetry,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._config = config
        self._telemetry = telemetry

    def finalize(
        self,
        context: DocumentContext,
        stopped_by: str | None,
        *,
        only_pages: Sequence[int] | None = None,
    ) -> DocumentOutcome:
        doc_id = context.record.doc_id
        scope = (
            set(only_pages) & set(context.universe)
            if only_pages is not None
            else set(context.universe)
        )
        for page in sorted(scope):
            if page not in context.processed:
                context.gaps[page] = PageGap(
                    doc_id=doc_id,
                    page_no=page,
                    defect_codes=(),
                    evidence=(
                        f"plan_stopped:{stopped_by}" if stopped_by else "unprocessed",
                    ),
                )

        fallback = resolve_fallback(
            self._config.fallback,
            available_enhancers=self._config.available_enhancers,
        )
        gaps = [context.gaps[page] for page in sorted(context.gaps) if page in scope]
        if fallback.path is FallbackPath.FAIL_DOC and gaps:
            self._telemetry.record_document(stp=False)
            log_event(
                "document_failed",
                doc_id=doc_id,
                gap_pages=len(gaps),
                reason=fallback.reason,
            )
            return DocumentOutcome(
                doc_id=doc_id,
                path=str(context.path),
                family=context.routing.family.value,
                status="failed",
                pages=len(scope),
                gap_pages=len(gaps),
                defect_codes=_gap_codes(gaps),
                fallback_path=fallback.path,
                note=fallback.reason,
                avg_extract_quality=self._avg_quality(context, scope),
                failure_counts=_failure_counts(context),
            )

        artifacts = [
            context.artifacts[page]
            for page in sorted(context.artifacts)
            if page in scope and page not in context.gaps
        ]
        qualities = {
            page: report.extract_quality
            for page, report in context.reports.items()
            if page in scope
        }
        document = merge_document(doc_id, artifacts, gaps, qualities=qualities)
        decision = decide_delivery(
            document, gap_tolerance=self._config.gap_tolerance
        )
        context.merged = document
        context.delivery = decision
        context.outputs = {
            fmt: str(self._output_dir / f"{doc_id}.{_SUFFIX[fmt]}")
            for fmt in self._config.output_formats
            if fmt in _SUFFIX
        }
        outcome = DocumentOutcome(
            doc_id=doc_id,
            path=str(context.path),
            family=context.routing.family.value,
            status="processed",
            pages=len(document.pages),
            gap_pages=len(document.gap_pages),
            level=decision.level,
            defect_codes=_gap_codes(gaps),
            outputs=tuple(context.outputs.values()),
            fallback_path=fallback.path if gaps else None,
            note=decision.report_note,
            avg_extract_quality=self._avg_quality(context, scope),
            failure_counts=_failure_counts(context),
        )
        self._telemetry.record_document(stp=not document.gap_pages)
        log_event(
            "document_finalized",
            doc_id=doc_id,
            pages=len(document.pages),
            gap_pages=len(document.gap_pages),
            delivery_level=decision.level.value,
        )
        return outcome
    def _avg_quality(self, context: DocumentContext, scope: set[int]) -> float:
        values = [
            report.extract_quality
            for page, report in context.reports.items()
            if page in scope
        ]
        return sum(values) / len(values) if values else 0.0
    def write_outputs(self, contexts: Sequence[DocumentContext]) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        for context in contexts:
            if context.merged is None or context.delivery is None:
                continue
            for fmt, target in context.outputs.items():
                if fmt == "json":
                    payload = to_json(context.merged, context.delivery).encode("utf-8")
                elif fmt == "markdown":
                    payload = to_markdown(context.merged, context.delivery).encode(
                        "utf-8"
                    )
                elif fmt == "csv":
                    payload = to_csv(context.merged, context.delivery).encode("utf-8")
                else:
                    payload = to_excel(context.merged, context.delivery)
                Path(target).write_bytes(payload)
    def report_inputs(
        self,
        contexts: Sequence[DocumentContext],
        outcomes: Mapping[str, DocumentOutcome],
    ) -> tuple[list[DocSummary], dict[str, list[PageDetail]]]:
        summaries = [
            DocSummary(
                doc_id=outcome.doc_id,
                pages=outcome.pages,
                gap_pages=outcome.gap_pages,
                level=outcome.level or DeliveryLevel.COMPLETE,
                failure_counts=outcome.failure_counts,
            )
            for outcome in outcomes.values()
            if outcome.status == "processed"
        ]
        details: dict[str, list[PageDetail]] = {}
        for context in contexts:
            if not context.gaps:
                continue
            details[context.record.doc_id] = [
                PageDetail(
                    doc_id=context.record.doc_id,
                    page_no=page,
                    extract_quality=(
                        context.reports[page].extract_quality
                        if page in context.reports
                        else 0.0
                    ),
                    defect_codes=gap.defect_codes,
                    fallback_used=gap.fallback_used,
                )
                for page, gap in sorted(context.gaps.items())
            ]
        return summaries, details
    def context_stats(
        self,
        contexts: Sequence[DocumentContext],
        total_pages: int,
        duration_s: float,
        stopped_by: str | None,
    ) -> TaskStats:
        """执行期统计（按上下文；用于 Plan 分支求值）。"""
        processed = sum(len(context.processed) for context in contexts)
        failed = sum(len(context.gaps) for context in contexts)
        quality_values = [
            report.extract_quality
            for context in contexts
            for report in context.reports.values()
        ]
        return TaskStats(
            pages_total=total_pages,
            pages_processed=processed,
            pages_failed=failed,
            stp_rate=(processed - failed) / processed if processed else 0.0,
            gap_ratio=failed / processed if processed else 0.0,
            avg_extract_quality=(
                sum(quality_values) / len(quality_values) if quality_values else 0.0
            ),
            duration_s=duration_s,
            stopped_by=stopped_by,
        )
    def stats(
        self,
        outcomes: Sequence[DocumentOutcome],
        total_pages: int,
        duration_s: float,
        stopped_by: str | None,
    ) -> TaskStats:
        attempted = [
            outcome
            for outcome in outcomes
            if outcome.status in {"processed", "failed"}
        ]
        processed = sum(outcome.pages for outcome in attempted)
        failed = sum(outcome.gap_pages for outcome in attempted)
        quality_values = [
            outcome.avg_extract_quality
            for outcome in attempted
            if outcome.status == "processed"
        ]
        return TaskStats(
            pages_total=total_pages,
            pages_processed=processed,
            pages_failed=failed,
            stp_rate=(processed - failed) / processed if processed else 0.0,
            gap_ratio=failed / processed if processed else 0.0,
            avg_extract_quality=(
                sum(quality_values) / len(quality_values) if quality_values else 0.0
            ),
            duration_s=duration_s,
            stopped_by=stopped_by,
        )
