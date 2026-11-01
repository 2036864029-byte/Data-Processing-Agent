"""控制面调度：状态机驱动 + 批次派发 + resume_point 续跑 + 批级事务边界。

宪法 VI / research D7：
- 任务沿 `kernel/state.py` 唯一转移表推进，恢复 = 重新执行当前状态动作，不新增恢复状态。
- 恢复从 `resume_point`（最后完成的批）之后继续，已完成批 0 重做。
- 批级事务：先把全部产物写对象存储（内容寻址、幂等），再写索引 / resume_point；
  两者都成功该批才算完成。索引写失败 → 该批未完成，重试时重新执行、内容幂等重写。

交互边界（contracts/role-messages.md）：批次派发使用 `ParseRequest`；
质检器回传的 `PageReport` 经 `qc_event()` 译为状态机事件。角色间禁止长同步等待。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from dataagent.contracts.messages import PageReport, ParseRequest, Verdict
from dataagent.kernel.state import Event, State, transition
from dataagent.platform.observe.alerts import Alert, AlertEvaluator, AlertSink

DEFAULT_BATCH_PAGES = 32


@dataclass(frozen=True, slots=True)
class Batch:
    """一个批级处理单元（页范围 1-indexed 闭区间）。"""

    index: int
    page_start: int
    page_end: int


@dataclass(frozen=True, slots=True)
class TaskRunContext:
    """派发批次所需的文档级上下文（幂等键组成项）。"""

    task_id: str
    doc_id: str
    engine: str
    params_hash: str
    rulepack_hash: str
    resource_hint: str = "cpu"
    doc_affinity_key: str = ""


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """批次产物引用（内容哈希；写对象存储前不落索引）。"""

    artifact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunReport:
    executed: tuple[int, ...]
    skipped: tuple[int, ...]


class BatchExecutor(Protocol):
    """解析批次执行器（进程池实现，对 supervisor 只暴露批级接口）。"""

    def execute(self, request: ParseRequest) -> BatchOutcome: ...


class ArtifactWriter(Protocol):
    """对象存储写入（内容寻址，幂等）。"""

    def write(self, artifact_id: str) -> None: ...


class ProgressRepository(Protocol):
    """PG 中的任务状态与 resume_point。"""

    def state(self, task_id: str) -> State: ...

    def save_state(self, task_id: str, state: State) -> None: ...

    def resume_point(self, task_id: str) -> int | None: ...

    def commit_batch(self, task_id: str, batch_index: int) -> None: ...


def plan_batches(
    page_count: int, *, batch_pages: int = DEFAULT_BATCH_PAGES
) -> tuple[Batch, ...]:
    """按批大小切分页范围；批级并行，禁页级 fan-out。"""
    if page_count < 0:
        raise ValueError("page_count 必须 ≥ 0")
    if batch_pages < 1:
        raise ValueError("batch_pages 必须 ≥ 1")
    return tuple(
        Batch(
            index=index,
            page_start=start,
            page_end=min(start + batch_pages - 1, page_count),
        )
        for index, start in enumerate(range(1, page_count + 1, batch_pages))
    )


def qc_event(report: PageReport) -> Event:
    """质检报告 → 状态机事件：pass 推进；fail / uncertain 均回到归因修复。"""
    return Event.QC_PASS if report.verdict is Verdict.PASS else Event.QC_FAIL


class Supervisor:
    """控制面：驱动状态机、按批派发解析、维护 resume_point。"""

    def __init__(
        self,
        *,
        executor: BatchExecutor,
        artifacts: ArtifactWriter,
        repository: ProgressRepository,
        alerts: AlertEvaluator | None = None,
        alert_sink: AlertSink | None = None,
    ) -> None:
        self._executor = executor
        self._artifacts = artifacts
        self._repository = repository
        self._alerts = alerts
        self._alert_sink = alert_sink

    def evaluate_alerts(self, stats: Mapping[str, float | int]) -> tuple[Alert, ...]:
        """告警触发点：批结束 / 任务报告生成时传入统计快照（FR-025）。"""
        if self._alerts is None:
            return ()
        alerts = self._alerts.evaluate(stats)
        if self._alert_sink is not None:
            for alert in alerts:
                self._alert_sink.emit(alert)
        return alerts

    def advance(self, task_id: str, event: Event) -> State:
        """沿唯一转移表推进并落库；非法转移抛 InvalidTransitionError。"""
        next_state = transition(self._repository.state(task_id), event)
        self._repository.save_state(task_id, next_state)
        return next_state

    def run_batches(
        self, context: TaskRunContext, batches: Sequence[Batch]
    ) -> RunReport:
        """执行 resume_point 之后的批；每批先写内容、再提交索引。"""
        resume = self._repository.resume_point(context.task_id)
        executed: list[int] = []
        skipped: list[int] = []

        for batch in batches:
            if resume is not None and batch.index <= resume:
                skipped.append(batch.index)
                continue

            outcome = self._executor.execute(_parse_request(context, batch))
            for artifact_id in outcome.artifact_ids:
                self._artifacts.write(artifact_id)
            self._repository.commit_batch(context.task_id, batch.index)
            executed.append(batch.index)

        return RunReport(executed=tuple(executed), skipped=tuple(skipped))


def _parse_request(context: TaskRunContext, batch: Batch) -> ParseRequest:
    return ParseRequest(
        task_id=context.task_id,
        doc_id=context.doc_id,
        page_range=(batch.page_start, batch.page_end),
        engine=context.engine,
        params_hash=context.params_hash,
        rulepack_hash=context.rulepack_hash,
        resource_hint=context.resource_hint,
        doc_affinity_key=context.doc_affinity_key,
    )
