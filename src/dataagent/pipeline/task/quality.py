"""页面质检与 T1 修复服务：只处理单页，不负责任务调度或交付。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from dataagent.contracts.artifacts import ExtractionResult, PageArtifact
from dataagent.contracts.defects import RepairTier
from dataagent.contracts.messages import PageReport, Verdict
from dataagent.contracts.policies import PdfType, ProfileQuality, ProfileSignals, Tier
from dataagent.kernel.policies.rule_tier import RuleTierPolicy
from dataagent.pipeline.profile.writer import PageProfileWriter
from dataagent.pipeline.quality.gate import QualityGate, QualityResultStore
from dataagent.pipeline.quality.l1_check import (
    L1Checker,
    L1Metrics,
    L1PageInput,
    defects_for_repair,
)
from dataagent.pipeline.quality.normalization import normalize_text
from dataagent.pipeline.quality.table_metrics import table_page_metrics
from dataagent.pipeline.repair.fallback import resolve_fallback
from dataagent.pipeline.repair.gap import PageGap, page_gap_from_outcome
from dataagent.pipeline.repair.loop import (
    EnhancementOutcome,
    LoopLimits,
    PageProbe,
    run_enhancement,
)
from dataagent.pipeline.repair.operators import OperatorContext
from dataagent.pipeline.sniff.sniffer import Family
from dataagent.pipeline.task.config import TaskConfig
from dataagent.pipeline.task.context import DocumentContext
from dataagent.pipeline.telemetry import TaskTelemetry
from dataagent.platform.observe.log import log_event, span

_STRATEGY_ROUNDS = {"quality": 3, "balanced": 2, "fast": 1}


def _synthetic_pass_report(doc_id: str, page_no: int) -> PageReport:
    return PageReport(
        doc_id=doc_id,
        page_no=page_no,
        verdict=Verdict.PASS,
        extract_quality=1.0,
        qc_confidence=1.0,
    )


def _repair_tier(tier: Tier | None) -> RepairTier | None:
    if tier is None or tier is Tier.T0:
        return None
    return RepairTier(tier.value)


class PageQualityService:
    def __init__(
        self,
        *,
        checker: L1Checker,
        gate: QualityGate,
        quality_store: QualityResultStore,
        profiles: PageProfileWriter,
        tier_policy: RuleTierPolicy,
        config: TaskConfig,
        telemetry: TaskTelemetry,
        clock: Callable[[], float],
    ) -> None:
        self._checker = checker
        self._gate = gate
        self._quality_store = quality_store
        self._profiles = profiles
        self._tier_policy = tier_policy
        self._config = config
        self._telemetry = telemetry
        self._clock = clock

    def mark_unsupported(self, context: DocumentContext, pages: Sequence[int]) -> None:
        """无对应引擎的家族（如图片链路）：直接记为缺口，不静默丢弃。"""
        for page in pages:
            context.gaps[page] = PageGap(
                doc_id=context.record.doc_id,
                page_no=page,
                defect_codes=(),
                evidence=("image_chain_unsupported",),
            )
            context.processed.add(page)

    def qualify(
        self,
        context: DocumentContext,
        artifact: PageArtifact,
        extraction: ExtractionResult,
        strategy: str,
    ) -> None:
        page = artifact.page_no
        if context.routing.family is Family.PDF:
            normalized = normalize_text(artifact.markdown)
            if normalized != artifact.markdown:
                artifact = artifact.model_copy(update={"markdown": normalized})
                context.artifacts[page] = artifact
        started = self._clock()
        with span(
            "quality",
            doc_id=context.record.doc_id,
            page_no=page,
            stage="quality",
        ):
            self._qualify_page(context, artifact, extraction, strategy)
        report = context.reports.get(page)
        tier = Tier.T0
        if report is not None:
            tier = self._tier_for(context, page, report) or Tier.T0
        is_complex = (
            page in extraction.pages_with_tables
            or page in extraction.pages_with_columns
            or extraction.is_complex
        )
        self._telemetry.record_page(
            tier=tier,
            latency_ms=(self._clock() - started) * 1000,
            is_complex=is_complex,
        )

    def _qualify_page(
        self,
        context: DocumentContext,
        artifact: PageArtifact,
        extraction: ExtractionResult,
        strategy: str,
    ) -> None:
        page = artifact.page_no
        if context.routing.family is not Family.PDF:
            report = _synthetic_pass_report(context.record.doc_id, page)
            self._quality_store.save(report)
            context.reports[page] = report
            context.processed.add(page)
            return

        page_input = self._page_input(context, artifact, extraction)
        report = self._gate.run_page(page_input)
        context.reports[page] = report

        if report.verdict is Verdict.PASS:
            self._write_quality_segment(context, page, page_input, report)
            context.processed.add(page)
            return

        attempted = bool(defects_for_repair(report))
        repaired = self._repair(context, artifact, page_input, report, strategy)
        if attempted:
            self._telemetry.record_postprocess(hit=not isinstance(repaired, EnhancementOutcome))
        if isinstance(repaired, EnhancementOutcome):
            self._write_gap(context, artifact, report, repaired)
        else:
            final_artifact, final_input, final_report = repaired
            context.artifacts[page] = final_artifact
            context.reports[page] = final_report
            self._gate.run_page(final_input)
            self._write_quality_segment(context, page, final_input, final_report)
        context.processed.add(page)

    def _repair(
        self,
        context: DocumentContext,
        artifact: PageArtifact,
        page_input: L1PageInput,
        report: PageReport,
        strategy: str,
    ) -> tuple[PageArtifact, L1PageInput, PageReport] | EnhancementOutcome:
        defects = defects_for_repair(report)
        if not defects:
            return EnhancementOutcome(
                artifact=artifact,
                remaining_defects=tuple(report.defects),
                actions=(),
                escalated=True,
                escalation_reason="no_operator",
                cost=0,
            )

        def probe(candidate: PageArtifact) -> PageProbe:
            candidate_input = L1PageInput(
                doc_id=page_input.doc_id,
                page_no=page_input.page_no,
                metrics=self._metrics_for(candidate.markdown, has_table=page_input.has_table),
                pdf_type=page_input.pdf_type,
                has_table=page_input.has_table,
                rotation=page_input.rotation,
            )
            candidate_report = self._checker.check(candidate_input)
            return PageProbe(
                quality=candidate_report.extract_quality,
                defects=defects_for_repair(candidate_report),
            )

        outcome = run_enhancement(
            artifact,
            defects,
            baseline_quality=report.extract_quality,
            probe=probe,
            limits=LoopLimits(max_rounds=_STRATEGY_ROUNDS.get(strategy, 2)),
            context=OperatorContext(
                doc_id=artifact.doc_id,
                page_no=artifact.page_no,
                previous_artifact=(
                    context.artifacts.get(artifact.page_no - 1) if artifact.page_no > 1 else None
                ),
            ),
        )
        if outcome.remaining_defects:
            return outcome

        final_input = L1PageInput(
            doc_id=page_input.doc_id,
            page_no=page_input.page_no,
            metrics=self._metrics_for(outcome.artifact.markdown, has_table=page_input.has_table),
            pdf_type=page_input.pdf_type,
            has_table=page_input.has_table,
            rotation=page_input.rotation,
        )
        return outcome.artifact, final_input, self._checker.check(final_input)

    def _write_gap(
        self,
        context: DocumentContext,
        artifact: PageArtifact,
        report: PageReport,
        outcome: EnhancementOutcome,
    ) -> None:
        fallback = resolve_fallback(
            self._config.fallback,
            available_enhancers=self._config.available_enhancers,
        )
        gap = page_gap_from_outcome(
            artifact,
            outcome,
            fallback=fallback.path,
            recommended_tier=_repair_tier(self._tier_for(context, artifact.page_no, report)),
        )
        context.gaps[artifact.page_no] = gap
        log_event(
            "page_gap",
            doc_id=artifact.doc_id,
            page_no=artifact.page_no,
            defect_codes=[code.value for code in gap.defect_codes],
            fallback=gap.fallback_used.value,
            tier=None if gap.recommended_tier is None else gap.recommended_tier.value,
        )

    def _tier_for(self, context: DocumentContext, page_no: int, report: PageReport) -> Tier | None:
        try:
            profile = self._profiles.get(context.record.doc_id, page_no)
        except KeyError:
            return None
        return self._tier_policy.tier(profile, report.extract_quality)

    def _write_quality_segment(
        self,
        context: DocumentContext,
        page_no: int,
        page_input: L1PageInput,
        report: PageReport,
    ) -> None:
        if context.routing.family is not Family.PDF:
            return
        metrics = page_input.metrics
        signals = ProfileSignals(
            char_density=metrics.char_density or 0.0,
            glyph_coverage=(metrics.glyph_coverage if metrics.glyph_coverage is not None else 1.0),
            empty_region_ratio=metrics.empty_region_ratio or 0.0,
            header_footer_ratio=metrics.header_footer_leak or 0.0,
        )
        try:
            self._profiles.write_quality(
                context.record.doc_id,
                page_no,
                signals=signals,
                quality=ProfileQuality(
                    extract_quality=report.extract_quality,
                    tier=self._tier_for(context, page_no, report),
                ),
            )
        except KeyError:
            return

    def _page_input(
        self,
        context: DocumentContext,
        artifact: PageArtifact,
        extraction: ExtractionResult,
    ) -> L1PageInput:
        try:
            pdf_type = self._profiles.get(context.record.doc_id, artifact.page_no).pdf_type
        except KeyError:
            pdf_type = PdfType.TEXT_BASED
        declared_table = artifact.page_no in extraction.pages_with_tables
        metrics = self._metrics_for(artifact.markdown, has_table=declared_table)
        return L1PageInput(
            doc_id=artifact.doc_id,
            page_no=artifact.page_no,
            metrics=metrics,
            pdf_type=pdf_type,
            # 伪表格已被过滤：只有真实表才算表格页，避免假缺口（R-0002）。
            has_table=metrics.table_cell_fill is not None,
            has_columns=artifact.page_no in extraction.pages_with_columns,
        )

    def _metrics_for(self, markdown: str, *, has_table: bool) -> L1Metrics:
        metrics = L1Metrics.from_text(markdown, text_area=self._config.text_area_cm2)
        if not has_table:
            return metrics
        table = table_page_metrics(
            markdown, skip_prose=True, policy=self._config.table_policy
        )
        if table is None:
            return metrics
        return replace(
            metrics,
            table_cell_fill=table.cell_fill,
            row_col_consistency=table.column_variance,
            table_header_ok=table.header_ok,
            row_occupancy=table.row_occupancy,
            col_occupancy=table.col_occupancy,
        )
