"""P0 端到端任务编排器（Phase 11 / T125）：B1–B8 全链路串联。

单任务流程：接入（去重 / 体检拦截）→ 格式判定与分派 → 两级路由 → 分批抽取
→ 画像两段写入（含文本层恢复）→ L1 质检 → T1 修复回路 → 合并 → 分级交付
→ 分层报告 + 多格式输出。

- 任何文件不得静默丢弃：rejected / pending / duplicate 都进入任务结果。
- 可选 Plan：先执行探路阶段，读统计指标后按冻结分支选择批量阶段（FR-026）；
  超出 guards 硬上限时停止执行，未处理页一律记为缺口。
- 所有依赖可注入（存储 / 注册表 / 质检器 / 时钟），便于测试与 worker 复用。

P0 口径说明：
- 字符密度按 A4 版心近似面积（468 cm²）计算，P1 由真实 bbox 替换；
- L2 抽样不在本编排器内（`quality/l2_sample.py` 独立可用），修复轮次随
  stage strategy 缩放（quality 3 / balanced 2 / fast 1）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from dataagent.contracts.artifacts import ExtractionResult, PageArtifact
from dataagent.contracts.defects import RepairTier
from dataagent.contracts.errors import DefectCode
from dataagent.contracts.messages import PageReport, Verdict
from dataagent.contracts.plugin import PluginContext
from dataagent.contracts.policies import (
    PdfType,
    ProfileQuality,
    ProfileSignals,
    StageKind,
    Tier,
)
from dataagent.kernel.plan import (
    DecisionRecord,
    FrozenPlan,
    PlanExecutor,
    PlanGuardExceeded,
    PlanResolution,
    PlanStageSpec,
    StageExecution,
)
from dataagent.kernel.policies.rule_route import RuleRoutePolicy
from dataagent.kernel.policies.rule_tier import RuleTierPolicy
from dataagent.kernel.registry import PluginRegistry
from dataagent.pipeline.ingest.ingestor import (
    DocumentRecord,
    Ingestor,
    IngestOutcome,
    PendingQueue,
    PendingRecord,
)
from dataagent.pipeline.merge.merger import MergedDocument, merge_document
from dataagent.pipeline.output.delivery import (
    DeliveryDecision,
    DeliveryLevel,
    decide_delivery,
)
from dataagent.pipeline.output.formats import to_csv, to_excel, to_json, to_markdown
from dataagent.pipeline.output.reports import (
    DocSummary,
    PageDetail,
    ProcessReport,
    build_report,
)
from dataagent.pipeline.profile.writer import InMemoryProfileStore, PageProfileWriter
from dataagent.pipeline.quality.gate import (
    InMemoryQualityResultStore,
    QualityGate,
    QualityResultStore,
)
from dataagent.pipeline.quality.l1_check import (
    L1Checker,
    L1Metrics,
    L1PageInput,
    defects_for_repair,
)
from dataagent.pipeline.quality.l2_sample import ProfileSpeed
from dataagent.pipeline.repair.fallback import (
    EnhancerKind,
    FallbackConfig,
    FallbackPath,
    resolve_fallback,
)
from dataagent.pipeline.repair.gap import PageGap, page_gap_from_outcome
from dataagent.pipeline.repair.loop import (
    EnhancementOutcome,
    LoopLimits,
    PageProbe,
    run_enhancement,
)
from dataagent.pipeline.route.routes import (
    InMemoryRouteDecisionStore,
    RegistryPdfClassifier,
    RoutingOutcome,
    TwoLevelRouter,
)
from dataagent.pipeline.sniff.dispatcher import DispatchAction, FormatDispatcher
from dataagent.pipeline.sniff.sniffer import Family, SniffResult, sniff_file

DEFAULT_BATCH_PAGES: Final[int] = 32
# P0 字符密度口径：A4 版心近似面积（cm²）；P1 由真实 bbox 替换
DEFAULT_TEXT_AREA_CM2: Final[float] = 468.0

_EXTRACT_CAPABILITIES: Final[Mapping[Family, str]] = {
    Family.PDF: "pdf.text.extract",
    Family.TABULAR: "tabular.text.extract",
    Family.TEXT: "text.text.extract",
}

_SUFFIX: Final[Mapping[str, str]] = {
    "json": "json",
    "markdown": "md",
    "csv": "csv",
    "excel": "xlsx",
}

_STRATEGY_ROUNDS: Final[Mapping[str, int]] = {
    "quality": 3,
    "balanced": 2,
    "fast": 1,
}

_DEFAULT_FALLBACK: Final[FallbackConfig] = FallbackConfig()


@dataclass(frozen=True, slots=True)
class TaskConfig:
    output_formats: tuple[str, ...] = ("json", "markdown")
    batch_pages: int = DEFAULT_BATCH_PAGES
    text_area_cm2: float = DEFAULT_TEXT_AREA_CM2
    gap_tolerance: float = 0.05
    fallback: FallbackConfig = _DEFAULT_FALLBACK
    available_enhancers: frozenset[EnhancerKind] = frozenset()
    profile_speed: ProfileSpeed = ProfileSpeed.BALANCED
    plan: FrozenPlan | None = None


@dataclass(frozen=True, slots=True)
class TaskStats:
    pages_total: int = 0
    pages_processed: int = 0
    pages_failed: int = 0
    stp_rate: float = 0.0
    gap_ratio: float = 0.0
    avg_extract_quality: float = 0.0
    duration_s: float = 0.0
    model_calls: int = 0
    stopped_by: str | None = None

    def as_plan_stats(self) -> dict[str, float | int]:
        """映射到 Plan 分支白名单指标（PLAN_METRICS 子集）。"""
        return {
            "stp_rate": self.stp_rate,
            "pages_total": self.pages_total,
            "pages_processed": self.pages_processed,
            "pages_failed": self.pages_failed,
            "gap_ratio": self.gap_ratio,
            "avg_extract_quality": self.avg_extract_quality,
            "elapsed_s": self.duration_s,
            "model_calls": self.model_calls,
            "cost_per_page": 0.0,
        }


@dataclass(frozen=True, slots=True)
class DocumentOutcome:
    doc_id: str
    path: str
    family: str
    status: str = "processed"
    pages: int = 0
    gap_pages: int = 0
    level: DeliveryLevel | None = None
    defect_codes: tuple[DefectCode, ...] = ()
    outputs: tuple[str, ...] = ()
    fallback_path: FallbackPath | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    documents: tuple[DocumentOutcome, ...]
    report: ProcessReport
    stats: TaskStats
    pending: tuple[PendingRecord, ...] = ()
    plan_resolution: PlanResolution | None = None
    decisions: tuple[DecisionRecord, ...] = ()

    def count(self, status: str) -> int:
        return sum(1 for document in self.documents if document.status == status)


@dataclass
class _DocContext:
    record: DocumentRecord
    path: Path
    sniff: SniffResult
    routing: RoutingOutcome
    universe: tuple[int, ...]
    artifacts: dict[int, PageArtifact] = field(default_factory=dict)
    reports: dict[int, PageReport] = field(default_factory=dict)
    gaps: dict[int, PageGap] = field(default_factory=dict)
    processed: set[int] = field(default_factory=set)
    merged: MergedDocument | None = None
    delivery: DeliveryDecision | None = None
    outputs: dict[str, str] = field(default_factory=dict)


class TaskRunner:
    """任务级编排器：一个任务 = 一批文件 + 可选冻结 Plan。"""

    def __init__(
        self,
        *,
        ingestor: Ingestor,
        dispatcher: FormatDispatcher,
        router: TwoLevelRouter,
        registry: PluginRegistry,
        profiles: PageProfileWriter,
        output_dir: Path | str,
        config: TaskConfig | None = None,
        checker: L1Checker | None = None,
        quality_store: QualityResultStore | None = None,
        tier_policy: RuleTierPolicy | None = None,
        pending: PendingQueue | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ingestor = ingestor
        self._dispatcher = dispatcher
        self._router = router
        self._registry = registry
        self._profiles = profiles
        self._output_dir = Path(output_dir)
        self._config = config if config is not None else TaskConfig()
        self._checker = checker if checker is not None else L1Checker()
        self._quality_store = (
            quality_store if quality_store is not None else InMemoryQualityResultStore()
        )
        self._gate = QualityGate(checker=self._checker, store=self._quality_store)
        self._tier_policy = tier_policy if tier_policy is not None else RuleTierPolicy()
        self._pending = pending
        self._clock = clock
        self._task_id = "task-local"
        self._reused: dict[str, DocumentOutcome] = {}
        self._contexts: dict[str, _DocContext] = {}
        self._duplicates: list[tuple[str, str]] = []

    def run(
        self,
        paths: Sequence[Path | str],
        *,
        task_id: str = "task-local",
    ) -> TaskOutcome:
        started = self._clock()
        self._task_id = task_id
        self._reused = {}
        self._contexts = {}

        outcomes: dict[str, DocumentOutcome] = {}
        analyzed: list[_DocContext] = []
        self._duplicates = []

        for path in paths:
            result = self._analyze(Path(path))
            if isinstance(result, DocumentOutcome):
                outcomes[result.path] = result
                continue
            analyzed.append(result)

        total_pages = sum(len(context.universe) for context in analyzed)
        resolution, decisions, stopped_by = self._execute_plan(analyzed, total_pages)

        for context in analyzed:
            outcomes[context.record.path] = self._finalize(context, stopped_by)

        # 同内容文件在分析阶段先于原件完成：此时原件结果已落定，回填复用结果
        for duplicate_path, doc_id in self._duplicates:
            reused = self._reused.get(doc_id)
            if reused is not None:
                outcomes[duplicate_path] = DocumentOutcome(
                    doc_id=reused.doc_id,
                    path=duplicate_path,
                    family=reused.family,
                    status="duplicate",
                    pages=reused.pages,
                    gap_pages=reused.gap_pages,
                    level=reused.level,
                    defect_codes=reused.defect_codes,
                    outputs=reused.outputs,
                    note="内容哈希重复，复用已有结果",
                )

        self._write_outputs(analyzed)
        stats = self._stats(
            analyzed, total_pages, self._clock() - started, stopped_by
        )
        summaries, details = self._report_inputs(analyzed)

        return TaskOutcome(
            documents=tuple(outcomes.values()),
            report=build_report(
                summaries,
                duration_s=stats.duration_s,
                page_details=details,
            ),
            stats=stats,
            pending=tuple(self._pending.all() if self._pending is not None else ()),
            plan_resolution=resolution,
            decisions=decisions,
        )

    def process_pages(
        self,
        path: Path | str,
        pages: Sequence[int],
        *,
        task_id: str = "task-local",
    ) -> DocumentOutcome:
        """worker 批处理入口：只处理给定页（内部 1-indexed），不标记其余页。"""
        self._task_id = task_id
        self._reused = {}
        self._contexts = {}
        self._duplicates = []
        result = self._analyze(Path(path))
        if isinstance(result, DocumentOutcome):
            return result
        requested = [page for page in pages if page in result.universe]
        self._process_stage(
            result, requested, self._config.profile_speed.value
        )
        outcome = self._finalize(result, None, only_pages=requested)
        self._write_outputs([result])
        return outcome

    # ── Phase A：接入 / 判定 / 路由 / 画像路由段 ──────────────────────────

    def _analyze(self, path: Path) -> _DocContext | DocumentOutcome:
        ingested = self._ingestor.ingest_path(path)
        if ingested.outcome is IngestOutcome.REJECTED:
            return DocumentOutcome(
                doc_id=ingested.document.doc_id,
                path=ingested.document.path,
                family="unknown",
                status="rejected",
                note="体检拦截，已进待确认队列",
            )
        if ingested.outcome is IngestOutcome.DUPLICATE:
            in_flight = self._contexts.get(ingested.document.doc_id)
            if in_flight is not None:
                self._duplicates.append((str(path), ingested.document.doc_id))
                return DocumentOutcome(
                    doc_id=ingested.document.doc_id,
                    path=str(path),
                    family=in_flight.routing.family.value,
                    status="duplicate",
                    note="内容哈希重复，复用已有结果",
                )
            reused = self._reused.get(ingested.document.doc_id)
            if reused is None:
                return DocumentOutcome(
                    doc_id=ingested.document.doc_id,
                    path=str(path),
                    family="unknown",
                    status="duplicate",
                    note="内容哈希重复，已有记录（本次未处理）",
                )
            return DocumentOutcome(
                doc_id=reused.doc_id,
                path=str(path),
                family=reused.family,
                status="duplicate",
                pages=reused.pages,
                gap_pages=reused.gap_pages,
                level=reused.level,
                defect_codes=reused.defect_codes,
                outputs=reused.outputs,
                note="内容哈希重复，复用已有结果",
            )

        record = ingested.document
        document_path = Path(record.path)
        sniff = sniff_file(document_path)
        dispatch = self._dispatcher.dispatch(record.doc_id, document_path, sniff)
        if dispatch.action is not DispatchAction.ROUTE:
            reason = dispatch.pending.reason if dispatch.pending is not None else "pending"
            return DocumentOutcome(
                doc_id=record.doc_id,
                path=str(document_path),
                family=dispatch.family.value,
                status="pending",
                note=reason,
            )

        routing = self._router.route(
            doc_id=record.doc_id, path=document_path, sniff=sniff
        )
        if routing.family is Family.PDF:
            for decision in routing.page_decisions:
                self._profiles.write_route(decision)
            page_count = (
                routing.classification.page_count
                if routing.classification is not None
                else 0
            )
            universe = tuple(range(1, page_count + 1))
        else:
            universe = (1,)
        context = _DocContext(
            record=record,
            path=document_path,
            sniff=sniff,
            routing=routing,
            universe=universe,
        )
        self._contexts[record.doc_id] = context
        return context

    # ── Phase B：Plan 阶段划分与执行 ────────────────────────────────────

    def _execute_plan(
        self,
        contexts: list[_DocContext],
        total_pages: int,
    ) -> tuple[PlanResolution | None, tuple[DecisionRecord, ...], str | None]:
        plan = self._config.plan
        if plan is None:
            self._run_stage(
                contexts, None, total_pages, self._config.profile_speed.value
            )
            return None, (), None

        probe_stages = _probe_stages(plan)
        for stage in probe_stages:
            self._run_stage(contexts, stage, total_pages, stage.strategy or "balanced")

        if not any(
            stage.kind is not StageKind.PROCESSING for stage in plan.spec.stages
        ):
            for stage in plan.spec.stages[len(probe_stages) :]:
                self._run_stage(
                    contexts, stage, total_pages, stage.strategy or "balanced"
                )
            stages = tuple(
                StageExecution(stage)
                for stage in plan.spec.stages
                if stage.kind is StageKind.PROCESSING
            )
            return PlanResolution(stages=stages, decisions=()), (), None

        executor = PlanExecutor(plan)
        stats = self._stats(contexts, total_pages, 0.0, None)
        try:
            resolution = executor.resolve(stats.as_plan_stats())
        except PlanGuardExceeded as exc:
            return exc.partial, exc.partial.decisions, exc.guard

        executed = {stage.id for stage in probe_stages}
        for execution in resolution.stages:
            if execution.id in executed:
                continue
            self._run_stage(
                contexts,
                execution.stage,
                total_pages,
                execution.stage.strategy or "balanced",
            )
        return resolution, resolution.decisions, None

    def _run_stage(
        self,
        contexts: list[_DocContext],
        stage: PlanStageSpec | None,
        total_pages: int,
        strategy: str,
    ) -> None:
        for context, pages in _stage_pages(contexts, stage, total_pages):
            if pages:
                self._process_stage(context, pages, strategy)

    # ── Phase C：单文档页处理 ──────────────────────────────────────────

    def _process_stage(
        self,
        context: _DocContext,
        pages: Sequence[int],
        strategy: str,
    ) -> None:
        ordered = sorted(page for page in pages if page in context.universe)
        for start in range(0, len(ordered), self._config.batch_pages):
            batch = ordered[start : start + self._config.batch_pages]
            self._extract_batch(context, batch, strategy)

    def _extract_batch(
        self,
        context: _DocContext,
        pages: Sequence[int],
        strategy: str,
    ) -> None:
        capability = _EXTRACT_CAPABILITIES.get(context.routing.family)
        if capability is None:
            for page in pages:
                context.gaps[page] = PageGap(
                    doc_id=context.record.doc_id,
                    page_no=page,
                    defect_codes=(),
                    evidence=("image_chain_unsupported",),
                )
                context.processed.add(page)
            return

        payload: dict[str, Any] = {"path": str(context.path), "pages": list(pages)}
        raw = self._registry.invoke(
            capability,
            payload,
            PluginContext(task_id=self._task_id, doc_id=context.record.doc_id),
        )
        extraction = ExtractionResult.model_validate(raw)
        for artifact in extraction.pages:
            context.artifacts[artifact.page_no] = artifact
            self._backfill_layout(context, artifact, extraction)
            self._qualify(context, artifact, extraction, strategy)

    def _backfill_layout(
        self,
        context: _DocContext,
        artifact: PageArtifact,
        extraction: ExtractionResult,
    ) -> None:
        if context.routing.family is not Family.PDF:
            return
        try:
            self._profiles.write_extraction_layout(
                context.record.doc_id,
                artifact.page_no,
                has_table=artifact.page_no in extraction.pages_with_tables,
                columns=2 if artifact.page_no in extraction.pages_with_columns else 1,
                is_complex=extraction.is_complex
                or artifact.page_no in extraction.pages_with_tables
                or artifact.page_no in extraction.pages_with_columns,
            )
            if "text_layer_recovered" in artifact.warnings:
                self._profiles.apply_text_layer_recovery(
                    context.record.doc_id, artifact.page_no
                )
        except KeyError:
            return

    def _qualify(
        self,
        context: _DocContext,
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

        repaired = self._repair(context, artifact, page_input, report, strategy)
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
        context: _DocContext,
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
                metrics=self._metrics_from(candidate.markdown),
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
        )
        if outcome.remaining_defects:
            return outcome

        final_input = L1PageInput(
            doc_id=page_input.doc_id,
            page_no=page_input.page_no,
            metrics=self._metrics_from(outcome.artifact.markdown),
            pdf_type=page_input.pdf_type,
            has_table=page_input.has_table,
            rotation=page_input.rotation,
        )
        return outcome.artifact, final_input, self._checker.check(final_input)

    def _write_gap(
        self,
        context: _DocContext,
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
            recommended_tier=_repair_tier(
                self._tier_for(context, artifact.page_no, report)
            ),
        )
        context.gaps[artifact.page_no] = gap

    def _tier_for(
        self, context: _DocContext, page_no: int, report: PageReport
    ) -> Tier | None:
        try:
            profile = self._profiles.get(context.record.doc_id, page_no)
        except KeyError:
            return None
        return self._tier_policy.tier(profile, report.extract_quality)

    def _write_quality_segment(
        self,
        context: _DocContext,
        page_no: int,
        page_input: L1PageInput,
        report: PageReport,
    ) -> None:
        if context.routing.family is not Family.PDF:
            return
        metrics = page_input.metrics
        signals = ProfileSignals(
            char_density=metrics.char_density or 0.0,
            glyph_coverage=(
                metrics.glyph_coverage if metrics.glyph_coverage is not None else 1.0
            ),
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
        context: _DocContext,
        artifact: PageArtifact,
        extraction: ExtractionResult,
    ) -> L1PageInput:
        try:
            pdf_type = self._profiles.get(
                context.record.doc_id, artifact.page_no
            ).pdf_type
        except KeyError:
            pdf_type = PdfType.TEXT_BASED
        return L1PageInput(
            doc_id=artifact.doc_id,
            page_no=artifact.page_no,
            metrics=self._metrics_from(artifact.markdown),
            pdf_type=pdf_type,
            has_table=artifact.page_no in extraction.pages_with_tables,
        )

    def _metrics_from(self, markdown: str) -> L1Metrics:
        return L1Metrics.from_text(markdown, text_area=self._config.text_area_cm2)

    # ── Phase D：合并 / 交付 / 输出 / 报告 ─────────────────────────────

    def _finalize(
        self,
        context: _DocContext,
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
        )
        self._reused[doc_id] = outcome
        return outcome

    def _write_outputs(self, contexts: Sequence[_DocContext]) -> None:
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

    def _report_inputs(
        self, contexts: Sequence[_DocContext]
    ) -> tuple[list[DocSummary], dict[str, list[PageDetail]]]:
        summaries: list[DocSummary] = []
        details: dict[str, list[PageDetail]] = {}
        for context in contexts:
            if context.merged is None or context.delivery is None:
                continue
            summaries.append(
                DocSummary(
                    doc_id=context.record.doc_id,
                    pages=len(context.merged.pages),
                    gap_pages=len(context.merged.gap_pages),
                    level=context.delivery.level,
                    failure_counts=_failure_counts(context),
                )
            )
            if context.gaps:
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

    def _stats(
        self,
        contexts: Sequence[_DocContext],
        total_pages: int,
        duration_s: float,
        stopped_by: str | None,
    ) -> TaskStats:
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


def _probe_stages(plan: FrozenPlan) -> list[PlanStageSpec]:
    probe: list[PlanStageSpec] = []
    for stage in plan.spec.stages:
        if stage.kind is not StageKind.PROCESSING:
            break
        probe.append(stage)
    return probe


def _stage_pages(
    contexts: Sequence[_DocContext],
    stage: PlanStageSpec | None,
    total_pages: int,
) -> list[tuple[_DocContext, list[int]]]:
    result: list[tuple[_DocContext, list[int]]] = []
    offset = 0
    for context in contexts:
        pages: list[int] = []
        for page in context.universe:
            offset += 1
            if page in context.processed:
                continue
            if stage is None or stage.scope is None or stage.scope.contains(offset, total_pages):
                pages.append(page)
        result.append((context, pages))
    return result


def _synthetic_pass_report(doc_id: str, page_no: int) -> PageReport:
    return PageReport(
        doc_id=doc_id,
        page_no=page_no,
        verdict=Verdict.PASS,
        extract_quality=1.0,
        qc_confidence=1.0,
    )


def _gap_codes(gaps: Sequence[PageGap]) -> tuple[DefectCode, ...]:
    return tuple(dict.fromkeys(code for gap in gaps for code in gap.defect_codes))


def _repair_tier(tier: Tier | None) -> RepairTier | None:
    if tier is None or tier is Tier.T0:
        return None
    return RepairTier(tier.value)


def _failure_counts(context: _DocContext) -> tuple[tuple[DefectCode, int], ...]:
    counts: dict[DefectCode, int] = {}
    for gap in context.gaps.values():
        for code in dict.fromkeys(gap.defect_codes):
            counts[code] = counts.get(code, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: item[0].value))


def create_builtin_runner(
    *,
    pending: PendingQueue | None = None,
    output_dir: Path | str = "var/output",
    config: TaskConfig | None = None,
) -> TaskRunner:
    """构造内置引擎的 runner（真实 PDF / CSV / TXT 链路，测试与 CLI 共用）。"""
    from dataagent.pipeline.ingest.ingestor import (
        InMemoryDocumentStore,
        InMemoryPendingQueue,
    )
    from dataagent.platform.plugins.adapters import builtin_plugins

    document_store = InMemoryDocumentStore()
    pending_queue = pending if pending is not None else InMemoryPendingQueue()
    registry = PluginRegistry()
    for plugin in builtin_plugins():
        registry.register(plugin, enable=True)
    classifier = RegistryPdfClassifier(
        registry, PluginContext(task_id="task-local", doc_id="classifier")
    )
    router = TwoLevelRouter(
        decisions=InMemoryRouteDecisionStore(),
        policy=RuleRoutePolicy(),
        classifier=classifier,
    )
    return TaskRunner(
        ingestor=Ingestor(documents=document_store, pending=pending_queue),
        dispatcher=FormatDispatcher(pending=pending_queue),
        router=router,
        registry=registry,
        profiles=PageProfileWriter(InMemoryProfileStore()),
        output_dir=output_dir,
        config=config,
        pending=pending_queue,
    )
