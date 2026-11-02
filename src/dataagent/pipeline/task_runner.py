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
from collections.abc import Callable, Sequence
from pathlib import Path

from dataagent.kernel.policies.rule_tier import RuleTierPolicy
from dataagent.kernel.registry import PluginRegistry
from dataagent.kernel.state import Event
from dataagent.kernel.supervisor import (
    InMemoryProgressRepository,
    ProgressRepository,
    Supervisor,
)
from dataagent.pipeline.ingest.ingestor import (
    Ingestor,
    PendingQueue,
)
from dataagent.pipeline.output.reports import (
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
)
from dataagent.pipeline.route.routes import (
    TwoLevelRouter,
)
from dataagent.pipeline.sniff.dispatcher import FormatDispatcher
from dataagent.pipeline.sniff.sniffer import Family
from dataagent.pipeline.task.config import TaskConfig
from dataagent.pipeline.task.context import DocumentContext
from dataagent.pipeline.task.control import (
    NoopArtifactWriter,
    NoopBatchExecutor,
    TaskControl,
)
from dataagent.pipeline.task.delivery import DocumentDeliveryService
from dataagent.pipeline.task.extract import ExtractionService
from dataagent.pipeline.task.intake import IntakeService
from dataagent.pipeline.task.plan_stage import PlanStageService
from dataagent.pipeline.task.quality import PageQualityService
from dataagent.pipeline.task.results import DocumentOutcome, TaskOutcome
from dataagent.pipeline.task.resume import (
    input_signature,
    load_resume_outcomes,
    mark_resumed,
    outcome_from_payload,
    persist_outcome,
)
from dataagent.pipeline.telemetry import TaskTelemetry
from dataagent.platform.observe.log import log_event, span
from dataagent.platform.store.sql.task_state import (
    InMemoryTaskStateStore,
    TaskStateStore,
)
from dataagent.platform.store.sql.tasks import TaskProgressStore


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
            executor=NoopBatchExecutor(),
            artifacts=NoopArtifactWriter(),
            repository=repository or InMemoryProgressRepository(),
        )
        self._control = TaskControl(
            supervisor=self._supervisor,
            telemetry=self._telemetry,
            progress=progress,
        )
        self._quality = PageQualityService(
            checker=self._checker,
            gate=self._gate,
            quality_store=self._quality_store,
            profiles=profiles,
            tier_policy=self._tier_policy,
            config=self._config,
            telemetry=self._telemetry,
            clock=clock,
        )
        self._delivery = DocumentDeliveryService(
            output_dir=output_dir,
            config=self._config,
            telemetry=self._telemetry,
        )
        self._plan_stage = PlanStageService(
            plan=self._config.plan,
            default_strategy=self._config.profile_speed.value,
            process_stage=self._process_stage,
            context_stats=lambda contexts, total: self._delivery.context_stats(
                contexts, total, 0.0, None
            ),
        )
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
        ordered_paths = sorted((Path(path) for path in paths), key=str)
        signature = input_signature(ordered_paths)
        stored = load_resume_outcomes(
            self._state_store,
            task_id,
            signature,
            resume=resume,
            plan_present=self._config.plan is not None,
        )
        self._control.begin(task_id)
        self._control.record_progress("running", done=0, total=0, detail={"files": len(paths)})

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

            resumed = mark_resumed(analyzed, stored)

            total_pages = sum(len(context.universe) for context in analyzed)
            if analyzed:
                self._control.advance(Event.INTEGRITY_OK)
                if any(context.routing.family is Family.PDF for context in analyzed):
                    self._control.advance(Event.FAMILY_PDF)
                    self._control.advance(Event.ROUTE_READY)
                else:
                    self._control.advance(Event.FAMILY_OTHER)
                self._control.advance(Event.QUEUED)
            else:
                self._control.advance(Event.INTEGRITY_FAIL)
            self._control.record_progress(
                "running",
                done=0,
                total=total_pages,
                detail={"files": len(paths), "documents": len(analyzed)},
            )
            execution = self._plan_stage.execute(analyzed, total_pages)
            resolution = execution.resolution
            decisions = execution.decisions
            stopped_by = execution.stopped_by
            if analyzed:
                self._control.advance(Event.PARSE_DONE)

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
                        outcome = self._delivery.finalize(context, stopped_by)
                    outcomes[context.record.path] = outcome
                    self._reused[outcome.doc_id] = outcome
                    persist_outcome(
                        self._state_store,
                        self._supervisor,
                        task_id,
                        index,
                        outcome,
                    )
                done_pages += len(context.universe)
                self._control.record_progress(
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
                self._delivery.write_outputs(analyzed)
            if analyzed:
                processed_docs = sum(1 for item in outcomes.values() if item.status == "processed")
                failed_docs = sum(1 for item in outcomes.values() if item.status == "failed")
                gap_pages = sum(item.gap_pages for item in outcomes.values())
                for event in self._control.completion_events(
                    processed_docs=processed_docs,
                    failed_docs=failed_docs,
                    gap_pages=gap_pages,
                ):
                    self._control.advance(event)
            stats = self._delivery.stats(
                list(outcomes.values()),
                total_pages,
                self._clock() - started,
                stopped_by,
            )
            summaries, details = self._delivery.report_inputs(analyzed, outcomes)
            alerts = self._telemetry.finish(task_id, extra={"gap_ratio": stats.gap_ratio})
            if alerts:
                log_event(
                    "alerts_triggered",
                    task_id=task_id,
                    count=len(alerts),
                    kinds=[alert.kind.value for alert in alerts],
                )
            self._control.record_progress(
                "finished",
                done=stats.pages_processed,
                total=stats.pages_total,
                detail={
                    "processed_docs": sum(
                        1 for item in outcomes.values() if item.status == "processed"
                    ),
                    "pending": sum(
                        1 for item in outcomes.values() if item.status in {"pending", "rejected"}
                    ),
                    "gap_pages": stats.pages_failed,
                    "duration_s": round(stats.duration_s, 3),
                },
            )
            log_event(
                "task_finished",
                task_id=task_id,
                documents=len(outcomes),
                processed=sum(1 for item in outcomes.values() if item.status == "processed"),
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
            machine_state=self._control.state.value,
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
        self._control.begin(task_id)
        self._control.record_progress("running", done=0, total=0, detail={"files": 1})
        with span("task", task_id=task_id, stage="task"):
            result = self._intake.analyze(Path(path))
            if isinstance(result, DocumentOutcome):
                self._control.advance(Event.INTEGRITY_FAIL)
                self._control.record_progress(
                    "finished",
                    done=0,
                    total=0,
                    detail={"status": result.status},
                )
                return result
            self._control.advance(Event.INTEGRITY_OK)
            if result.routing.family is Family.PDF:
                self._control.advance(Event.FAMILY_PDF)
                self._control.advance(Event.ROUTE_READY)
            else:
                self._control.advance(Event.FAMILY_OTHER)
            self._control.advance(Event.QUEUED)
            requested = [page for page in pages if page in result.universe]
            self._process_stage(result, requested, self._config.profile_speed.value)
            self._control.advance(Event.PARSE_DONE)
            with span("finalize", doc_id=result.record.doc_id, stage="merge"):
                outcome = self._delivery.finalize(result, None, only_pages=requested)
            for event in self._control.completion_events(
                processed_docs=1 if outcome.status == "processed" else 0,
                failed_docs=1 if outcome.status == "failed" else 0,
                gap_pages=outcome.gap_pages,
            ):
                self._control.advance(event)
            with span("output", stage="output"):
                self._delivery.write_outputs([result])
            self._control.finish()
            self._control.record_progress(
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

    # ── Phase B：Plan 阶段划分与执行 ────────────────────────────────────

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
        if not self._extractor.supports(context.routing.family):
            self._quality.mark_unsupported(context, pages)
            return

        with span(
            "extract",
            doc_id=context.record.doc_id,
            stage="extract",
            page_start=pages[0],
            page_end=pages[-1],
        ):
            extraction = self._extractor.extract(context, pages, task_id=self._task_id)
        for artifact in extraction.pages:
            context.artifacts[artifact.page_no] = artifact
            self._extractor.backfill_layout(context, artifact, extraction)
            self._quality.qualify(context, artifact, extraction, strategy)

    # ── Phase D：合并 / 交付 / 输出 / 报告 ─────────────────────────────
