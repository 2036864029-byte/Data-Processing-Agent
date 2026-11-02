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

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

from dataagent.contracts.artifacts import ExtractionResult, PageArtifact
from dataagent.contracts.defects import RepairTier
from dataagent.contracts.errors import DefectCode
from dataagent.contracts.messages import PageReport, ParseRequest, Verdict
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
from dataagent.kernel.policies.rule_tier import RuleTierPolicy
from dataagent.kernel.registry import PluginRegistry
from dataagent.kernel.state import Event, State
from dataagent.kernel.supervisor import (
    BatchOutcome,
    InMemoryProgressRepository,
    ProgressRepository,
    Supervisor,
)
from dataagent.pipeline.ingest.ingestor import (
    Ingestor,
    PendingQueue,
)
from dataagent.pipeline.merge.merger import merge_document
from dataagent.pipeline.output.delivery import (
    DeliveryLevel,
    decide_delivery,
)
from dataagent.pipeline.output.formats import to_csv, to_excel, to_json, to_markdown
from dataagent.pipeline.output.reports import (
    DocSummary,
    PageDetail,
    build_report,
)
from dataagent.pipeline.profile.writer import PageProfileWriter
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
from dataagent.pipeline.quality.table_metrics import table_page_metrics
from dataagent.pipeline.repair.fallback import (
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
from dataagent.pipeline.repair.operators import OperatorContext
from dataagent.pipeline.route.routes import (
    TwoLevelRouter,
)
from dataagent.pipeline.sniff.dispatcher import FormatDispatcher
from dataagent.pipeline.sniff.sniffer import Family
from dataagent.pipeline.task.context import DocumentContext
from dataagent.pipeline.task.extract import EXTRACT_CAPABILITIES, ExtractionService
from dataagent.pipeline.task.intake import IntakeService
from dataagent.pipeline.task.models import (
    DocumentOutcome,
    TaskConfig,
    TaskOutcome,
    TaskStats,
)
from dataagent.pipeline.task.resume import (
    input_signature,
    outcome_from_payload,
    outcome_to_payload,
)
from dataagent.pipeline.telemetry import TaskTelemetry
from dataagent.platform.observe.log import log_event, span
from dataagent.platform.store.sql.task_state import (
    InMemoryTaskStateStore,
    TaskStateStore,
)
from dataagent.platform.store.sql.tasks import TaskProgress, TaskProgressStore

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
        telemetry: TaskTelemetry | None = None,
        progress: TaskProgressStore | None = None,
        supervisor: Supervisor | None = None,
        state_repository: ProgressRepository | None = None,
        state_store: TaskStateStore | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
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
        self._telemetry = telemetry if telemetry is not None else TaskTelemetry()
        self._intake = IntakeService(
            ingestor=ingestor,
            dispatcher=dispatcher,
            router=router,
            profiles=profiles,
            telemetry=self._telemetry,
        )
        self._extractor = ExtractionService(registry=registry, profiles=profiles)
        self._progress = progress
        if state_store is None and isinstance(state_repository, TaskStateStore):
            state_store = state_repository
        if state_store is None and state_repository is None:
            state_store = InMemoryTaskStateStore()
        self._state_store = state_store
        repository: ProgressRepository | None = (
            state_store if state_store is not None else state_repository
        )
        self._supervisor = supervisor or Supervisor(
            executor=_NoopBatchExecutor(),
            artifacts=_NoopArtifactWriter(),
            repository=repository or InMemoryProgressRepository(),
        )
        self._state = State.RECEIVED
        self._clock = clock
        self._task_id = "task-local"
        self._reused: dict[str, DocumentOutcome] = {}
        self._duplicates: list[tuple[str, str]] = []

    def run(
        self,
        paths: Sequence[Path | str],
        *,
        task_id: str = "task-local",
        resume: bool = True,
    ) -> TaskOutcome:
        started = self._clock()
        self._task_id = task_id
        self._reused = {}
        self._telemetry.reset()
        ordered_paths = sorted((Path(path) for path in paths), key=str)
        signature = input_signature(ordered_paths)
        stored: list[dict[str, Any]] = []
        if self._state_store is not None:
            if (
                resume
                and self._config.plan is None
                and self._state_store.input_signature(task_id) == signature
            ):
                stored = self._state_store.outcomes(task_id)
            else:
                self._state_store.reset_outcomes(task_id)
                self._state_store.set_input_signature(task_id, signature)
        self._state = self._supervisor.begin_task(task_id)
        self._record_progress(
            "running", done=0, total=0, detail={"files": len(paths)}
        )

        outcomes: dict[str, DocumentOutcome] = {}
        analyzed: list[DocumentContext] = []
        self._duplicates = []

        with span("task", task_id=task_id, stage="task"):
            log_event("task_started", task_id=task_id, files=len(paths))
            with span("intake", stage="ingest"):
                batch = self._intake.analyze_all(ordered_paths)
                analyzed = batch.contexts
                self._duplicates = batch.duplicates
                for outcome in batch.outcomes:
                    outcomes[outcome.path] = outcome

            resumed = 0
            for payload in stored[: len(analyzed)]:
                if not payload:
                    break
                resumed += 1
            for context in analyzed[:resumed]:
                context.processed.update(context.universe)

            total_pages = sum(len(context.universe) for context in analyzed)
            if analyzed:
                self._advance(task_id, Event.INTEGRITY_OK)
                if any(
                    context.routing.family is Family.PDF
                    for context in analyzed
                ):
                    self._advance(task_id, Event.FAMILY_PDF)
                    self._advance(task_id, Event.ROUTE_READY)
                else:
                    self._advance(task_id, Event.FAMILY_OTHER)
                self._advance(task_id, Event.QUEUED)
            else:
                self._advance(task_id, Event.INTEGRITY_FAIL)
            self._record_progress(
                "running",
                done=0,
                total=total_pages,
                detail={"files": len(paths), "documents": len(analyzed)},
            )
            resolution, decisions, stopped_by = self._execute_plan(
                analyzed, total_pages
            )
            if analyzed:
                self._advance(task_id, Event.PARSE_DONE)

            done_pages = 0
            for index, context in enumerate(analyzed):
                if index < resumed:
                    restored = outcome_from_payload(stored[index])
                    outcomes[context.record.path] = restored
                    self._reused[restored.doc_id] = restored
                else:
                    with span(
                        "finalize",
                        doc_id=context.record.doc_id,
                        stage="merge",
                    ):
                        outcome = self._finalize(context, stopped_by)
                    outcomes[context.record.path] = outcome
                    if self._state_store is not None:
                        self._state_store.save_outcome(
                            task_id, index, outcome_to_payload(outcome)
                        )
                        self._supervisor.commit_batch(task_id, index)
                done_pages += len(context.universe)
                self._record_progress(
                    "running",
                    done=done_pages,
                    total=total_pages,
                    detail={
                        "documents_done": index + 1,
                        "documents_total": len(analyzed),
                    },
                )

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

            with span("output", stage="output"):
                self._write_outputs(analyzed)
            if analyzed:
                processed_docs = sum(
                    1 for item in outcomes.values() if item.status == "processed"
                )
                failed_docs = sum(
                    1 for item in outcomes.values() if item.status == "failed"
                )
                gap_pages = sum(
                    item.gap_pages for item in outcomes.values()
                )
                for event in self._completion_events(
                    processed_docs=processed_docs,
                    failed_docs=failed_docs,
                    gap_pages=gap_pages,
                ):
                    self._advance(task_id, event)
            stats = self._stats(
                list(outcomes.values()),
                total_pages,
                self._clock() - started,
                stopped_by,
            )
            summaries, details = self._report_inputs(analyzed, outcomes)
            alerts = self._telemetry.finish(
                task_id, extra={"gap_ratio": stats.gap_ratio}
            )
            if alerts:
                log_event(
                    "alerts_triggered",
                    task_id=task_id,
                    count=len(alerts),
                    kinds=[alert.kind.value for alert in alerts],
                )
            self._record_progress(
                "finished",
                done=stats.pages_processed,
                total=stats.pages_total,
                detail={
                    "processed_docs": sum(
                        1 for item in outcomes.values() if item.status == "processed"
                    ),
                    "pending": sum(
                        1
                        for item in outcomes.values()
                        if item.status in {"pending", "rejected"}
                    ),
                    "gap_pages": stats.pages_failed,
                    "duration_s": round(stats.duration_s, 3),
                },
            )
            log_event(
                "task_finished",
                task_id=task_id,
                documents=len(outcomes),
                processed=sum(
                    1 for item in outcomes.values() if item.status == "processed"
                ),
                gap_pages=stats.pages_failed,
                duration_s=round(stats.duration_s, 3),
            )

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
            machine_state=self._state.value,
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
        self._duplicates = []
        self._telemetry.reset()
        self._state = self._supervisor.begin_task(task_id)
        self._record_progress(
            "running", done=0, total=0, detail={"files": 1}
        )
        with span("task", task_id=task_id, stage="task"):
            result = self._intake.analyze(Path(path))
            if isinstance(result, DocumentOutcome):
                self._advance(task_id, Event.INTEGRITY_FAIL)
                self._record_progress(
                    "finished",
                    done=0,
                    total=0,
                    detail={"status": result.status},
                )
                return result
            self._advance(task_id, Event.INTEGRITY_OK)
            if result.routing.family is Family.PDF:
                self._advance(task_id, Event.FAMILY_PDF)
                self._advance(task_id, Event.ROUTE_READY)
            else:
                self._advance(task_id, Event.FAMILY_OTHER)
            self._advance(task_id, Event.QUEUED)
            requested = [page for page in pages if page in result.universe]
            self._process_stage(
                result, requested, self._config.profile_speed.value
            )
            self._advance(task_id, Event.PARSE_DONE)
            with span("finalize", doc_id=result.record.doc_id, stage="merge"):
                outcome = self._finalize(result, None, only_pages=requested)
            for event in self._completion_events(
                processed_docs=1 if outcome.status == "processed" else 0,
                failed_docs=1 if outcome.status == "failed" else 0,
                gap_pages=outcome.gap_pages,
            ):
                self._advance(task_id, event)
            with span("output", stage="output"):
                self._write_outputs([result])
            self._telemetry.finish(task_id)
            self._record_progress(
                "finished",
                done=len(requested),
                total=len(result.universe),
                detail={
                    "status": outcome.status,
                    "gap_pages": outcome.gap_pages,
                },
            )
            return outcome

    # ── Phase A：接入 / 判定 / 路由 / 画像路由段 ──────────────────────────



    def _advance(self, task_id: str, event: Event) -> State:
        """沿 kernel 唯一状态表推进任务（控制面驱动，不再各自维护状态）。"""
        self._state = self._supervisor.advance(task_id, event)
        log_event(
            "state_transition",
            task_id=task_id,
            state=self._state.value,
            transition_event=event.value,
        )
        return self._state

    def _completion_events(
        self, *, processed_docs: int, failed_docs: int, gap_pages: int
    ) -> list[Event]:
        """任务级状态投影：页级细节收敛为一条合法路径。

        注意：这是任务级状态机，不逐页建模档位；TIER_T1 用来表示
        “已进入后处理/修复阶段”。全部失败 → ESCALATED；有缺口 →
        ATTRIBUTED → T1 修复 → L1 通过；无缺口 → 一次通过。
        """
        if processed_docs == 0 and failed_docs > 0:
            return [
                Event.TIER_T1,
                Event.POSTPROCESS_DONE,
                Event.QC_FAIL,
                Event.UNRESOLVED,
            ]
        events = [Event.TIER_T1, Event.POSTPROCESS_DONE]
        if gap_pages > 0:
            events.extend([Event.QC_FAIL, Event.ACTION_T1, Event.POSTPROCESS_DONE])
        events.extend(
            [
                Event.QC_PASS,
                Event.QC_PASS,
                Event.MERGE_DONE,
                Event.QC_PASS,
            ]
        )
        return events

    def _record_progress(
        self,
        state: str,
        *,
        done: int,
        total: int,
        detail: Mapping[str, Any],
    ) -> None:
        """任务进度 best-effort 写入：失败只记日志，不影响主链路。"""
        if self._progress is None:
            return
        payload = {**detail, "machine_state": self._state.value}
        try:
            self._progress.upsert(
                TaskProgress(
                    task_id=self._task_id,
                    state=state,
                    progress=f"{done}/{total}",
                    pages_total=total,
                    pages_done=done,
                    detail=payload,
                )
            )
        except Exception as exc:
            log_event(
                "progress_write_failed",
                level=logging.WARNING,
                task_id=self._task_id,
                error=str(exc),
            )

    # ── Phase B：Plan 阶段划分与执行 ────────────────────────────────────

    def _execute_plan(
        self,
        contexts: list[DocumentContext],
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
        stats = self._context_stats(contexts, total_pages, 0.0, None)
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
        contexts: list[DocumentContext],
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
        context: DocumentContext,
        pages: Sequence[int],
        strategy: str,
    ) -> None:
        ordered = sorted(page for page in pages if page in context.universe)
        for start in range(0, len(ordered), self._config.batch_pages):
            batch = ordered[start : start + self._config.batch_pages]
            self._extract_batch(context, batch, strategy)

    def _extract_batch(
        self,
        context: DocumentContext,
        pages: Sequence[int],
        strategy: str,
    ) -> None:
        capability = EXTRACT_CAPABILITIES.get(context.routing.family)
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

        with span(
            "extract",
            doc_id=context.record.doc_id,
            stage="extract",
            page_start=pages[0],
            page_end=pages[-1],
        ):
            extraction = self._extractor.extract(
                context, pages, task_id=self._task_id
            )
        for artifact in extraction.pages:
            context.artifacts[artifact.page_no] = artifact
            self._extractor.backfill_layout(context, artifact, extraction)
            self._qualify(context, artifact, extraction, strategy)


    def _qualify(
        self,
        context: DocumentContext,
        artifact: PageArtifact,
        extraction: ExtractionResult,
        strategy: str,
    ) -> None:
        page = artifact.page_no
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
            self._telemetry.record_postprocess(
                hit=not isinstance(repaired, EnhancementOutcome)
            )
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
                metrics=self._metrics_for(
                    candidate.markdown, has_table=page_input.has_table
                ),
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
                    context.artifacts.get(artifact.page_no - 1)
                    if artifact.page_no > 1
                    else None
                ),
            ),
        )
        if outcome.remaining_defects:
            return outcome

        final_input = L1PageInput(
            doc_id=page_input.doc_id,
            page_no=page_input.page_no,
            metrics=self._metrics_for(
                outcome.artifact.markdown, has_table=page_input.has_table
            ),
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
            recommended_tier=_repair_tier(
                self._tier_for(context, artifact.page_no, report)
            ),
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

    def _tier_for(
        self, context: DocumentContext, page_no: int, report: PageReport
    ) -> Tier | None:
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
        context: DocumentContext,
        artifact: PageArtifact,
        extraction: ExtractionResult,
    ) -> L1PageInput:
        try:
            pdf_type = self._profiles.get(
                context.record.doc_id, artifact.page_no
            ).pdf_type
        except KeyError:
            pdf_type = PdfType.TEXT_BASED
        has_table = artifact.page_no in extraction.pages_with_tables
        return L1PageInput(
            doc_id=artifact.doc_id,
            page_no=artifact.page_no,
            metrics=self._metrics_for(artifact.markdown, has_table=has_table),
            pdf_type=pdf_type,
            has_table=has_table,
        )

    def _metrics_for(self, markdown: str, *, has_table: bool) -> L1Metrics:
        metrics = L1Metrics.from_text(
            markdown, text_area=self._config.text_area_cm2
        )
        if not has_table:
            return metrics
        table = table_page_metrics(markdown)
        if table is None:
            return metrics
        return replace(
            metrics,
            table_cell_fill=table.cell_fill,
            row_col_consistency=table.column_variance,
            table_header_ok=table.header_ok,
        )

    # ── Phase D：合并 / 交付 / 输出 / 报告 ─────────────────────────────

    def _finalize(
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
        self._reused[doc_id] = outcome
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

    def _write_outputs(self, contexts: Sequence[DocumentContext]) -> None:
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

    def _context_stats(
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

    def _stats(
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


class _NoopBatchExecutor:
    """状态机接入用：runner 自身执行解析，supervisor 只驱动状态。"""

    def execute(self, request: ParseRequest) -> BatchOutcome:
        del request
        return BatchOutcome(artifact_ids=())


class _NoopArtifactWriter:
    def write(self, artifact_id: str) -> None:
        del artifact_id


def _probe_stages(plan: FrozenPlan) -> list[PlanStageSpec]:
    probe: list[PlanStageSpec] = []
    for stage in plan.spec.stages:
        if stage.kind is not StageKind.PROCESSING:
            break
        probe.append(stage)
    return probe


def _stage_pages(
    contexts: Sequence[DocumentContext],
    stage: PlanStageSpec | None,
    total_pages: int,
) -> list[tuple[DocumentContext, list[int]]]:
    result: list[tuple[DocumentContext, list[int]]] = []
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


def _failure_counts(context: DocumentContext) -> tuple[tuple[DefectCode, int], ...]:
    counts: dict[DefectCode, int] = {}
    for gap in context.gaps.values():
        for code in dict.fromkeys(gap.defect_codes):
            counts[code] = counts.get(code, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: item[0].value))
