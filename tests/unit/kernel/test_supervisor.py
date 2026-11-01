"""supervisor 状态机 / 续跑 / 批级事务边界测试（T037，A6）。

- 任务沿唯一转移表推进（不新增恢复状态）。
- 恢复从 resume_point 后的批继续、已完成批 0 重做（SC-010）。
- 批级事务边界：先写内容再写索引；产物 + 索引都成功才算批完成（G6）。
"""

from dataclasses import dataclass, field

import pytest

from dataagent.contracts.messages import PageReport, ParseRequest, Verdict
from dataagent.kernel.state import Event, InvalidTransitionError, State
from dataagent.kernel.supervisor import (
    BatchOutcome,
    Supervisor,
    TaskRunContext,
    plan_batches,
    qc_event,
)

CONTEXT = TaskRunContext(
    task_id="task-1",
    doc_id="sha256:doc",
    engine="engine.pdf@1.0.0",
    params_hash="params-hash",
    rulepack_hash="rulepack-hash",
    resource_hint="cpu",
    doc_affinity_key="affinity-key",
)


@dataclass
class InMemoryRepository:
    states: dict[str, State]
    log: list[str] = field(default_factory=list)
    resume_points: dict[str, int] = field(default_factory=dict)
    commits: list[tuple[str, int]] = field(default_factory=list)
    fail_commit_once: bool = False

    def state(self, task_id: str) -> State:
        return self.states[task_id]

    def save_state(self, task_id: str, state: State) -> None:
        self.states[task_id] = state

    def resume_point(self, task_id: str) -> int | None:
        return self.resume_points.get(task_id)

    def commit_batch(self, task_id: str, batch_index: int) -> None:
        if self.fail_commit_once:
            self.fail_commit_once = False
            raise RuntimeError("index write failed")
        self.resume_points[task_id] = batch_index
        self.commits.append((task_id, batch_index))
        self.log.append(f"index:{task_id}:{batch_index}")


@dataclass
class RecordingExecutor:
    outcomes: dict[int, tuple[str, ...]] = field(default_factory=dict)
    calls: list[ParseRequest] = field(default_factory=list)

    def execute(self, request: ParseRequest) -> BatchOutcome:
        self.calls.append(request)
        artifact_ids = self.outcomes.get(request.page_range[0], ("sha256:default",))
        return BatchOutcome(artifact_ids=artifact_ids)


@dataclass
class RecordingArtifacts:
    log: list[str]
    written: list[str] = field(default_factory=list)
    fail_on: str | None = None

    def write(self, artifact_id: str) -> None:
        if self.fail_on == artifact_id:
            raise RuntimeError("content write failed")
        self.written.append(artifact_id)
        self.log.append(f"content:{artifact_id}")


def make_supervisor(
    *, state: State = State.PARSING, resume: int | None = None
) -> tuple[Supervisor, InMemoryRepository, RecordingExecutor, RecordingArtifacts]:
    log: list[str] = []
    repository = InMemoryRepository(states={"task-1": state}, log=log)
    if resume is not None:
        repository.resume_points["task-1"] = resume
    executor = RecordingExecutor()
    artifacts = RecordingArtifacts(log=log)
    supervisor = Supervisor(
        executor=executor, artifacts=artifacts, repository=repository
    )
    return supervisor, repository, executor, artifacts


def test_advance_follows_unique_transition_table() -> None:
    """任务沿唯一转移表推进，每次转移落库。"""
    supervisor, repository, _, _ = make_supervisor(state=State.RECEIVED)
    path = [
        (Event.INTEGRITY_OK, State.SNIFFED),
        (Event.FAMILY_PDF, State.CLASSIFIED),
        (Event.ROUTE_READY, State.ROUTED),
        (Event.QUEUED, State.PARSING),
        (Event.PARSE_DONE, State.PARSED),
        (Event.TIER_T0, State.L1_CHECK),
        (Event.QC_PASS, State.L2_CHECK),
        (Event.QC_PASS, State.MERGED),
        (Event.MERGE_DONE, State.L3_CHECK),
        (Event.QC_PASS, State.PUBLISHED),
    ]
    for event, expected in path:
        assert supervisor.advance("task-1", event) is expected
        assert repository.states["task-1"] is expected


def test_advance_rejects_invalid_transition_and_keeps_state() -> None:
    """非法转移抛 InvalidTransitionError，状态不落库。"""
    supervisor, repository, _, _ = make_supervisor(state=State.RECEIVED)
    with pytest.raises(InvalidTransitionError):
        supervisor.advance("task-1", Event.QC_PASS)
    assert repository.states["task-1"] is State.RECEIVED


def test_plan_batches_splits_by_batch_size() -> None:
    """按批大小切分页范围（默认 32 页；批级并行，禁页级 fan-out）。"""
    batches = plan_batches(70)
    assert [(b.index, b.page_start, b.page_end) for b in batches] == [
        (0, 1, 32),
        (1, 33, 64),
        (2, 65, 70),
    ]
    assert plan_batches(0) == ()
    with pytest.raises(ValueError):
        plan_batches(-1)
    with pytest.raises(ValueError):
        plan_batches(10, batch_pages=0)


def test_resume_skips_completed_batches_and_never_redoes_them() -> None:
    """恢复从 resume_point 后的批继续；已完成批 0 重做（SC-010）。"""
    supervisor, _, executor, _ = make_supervisor(resume=2)
    report = supervisor.run_batches(CONTEXT, plan_batches(160))

    assert report.executed == (3, 4)
    assert report.skipped == (0, 1, 2)
    assert [request.page_range for request in executor.calls] == [(97, 128), (129, 160)]


def test_without_resume_point_all_batches_run() -> None:
    supervisor, _, executor, _ = make_supervisor()
    report = supervisor.run_batches(CONTEXT, plan_batches(64))

    assert report.executed == (0, 1)
    assert report.skipped == ()
    assert len(executor.calls) == 2


def test_batch_writes_content_before_index() -> None:
    """批级事务：先写内容（对象存储）再写索引（PG），顺序可观测。"""
    supervisor, repository, executor, _ = make_supervisor()
    executor.outcomes[1] = ("sha256:a", "sha256:b")

    supervisor.run_batches(CONTEXT, plan_batches(32))

    assert repository.log == [
        "content:sha256:a",
        "content:sha256:b",
        "index:task-1:0",
    ]
    assert repository.resume_point("task-1") == 0


def test_content_write_failure_prevents_index_commit() -> None:
    """产物未全部写入时不得提交索引——该批不算完成。"""
    supervisor, repository, executor, artifacts = make_supervisor()
    executor.outcomes[1] = ("sha256:a", "sha256:b")
    artifacts.fail_on = "sha256:b"

    with pytest.raises(RuntimeError):
        supervisor.run_batches(CONTEXT, plan_batches(32))

    assert repository.commits == []
    assert repository.resume_point("task-1") is None


def test_index_failure_leaves_batch_pending_and_retry_redoes_it() -> None:
    """索引写失败则该批未完成；重试时重新执行该批（内容写入幂等）。"""
    supervisor, repository, executor, _ = make_supervisor()
    batches = plan_batches(32)
    repository.fail_commit_once = True

    with pytest.raises(RuntimeError):
        supervisor.run_batches(CONTEXT, batches)
    assert repository.resume_point("task-1") is None

    report = supervisor.run_batches(CONTEXT, batches)
    assert report.executed == (0,)
    assert len(executor.calls) == 2
    assert repository.resume_point("task-1") == 0


def test_parse_request_matches_role_message_contract() -> None:
    """批次派发以 ParseRequest 为边界，字段逐字来自 role-messages.md。"""
    supervisor, _, executor, _ = make_supervisor()
    supervisor.run_batches(CONTEXT, plan_batches(32))

    request = executor.calls[0]
    assert request.task_id == "task-1"
    assert request.doc_id == "sha256:doc"
    assert request.page_range == (1, 32)
    assert request.engine == "engine.pdf@1.0.0"
    assert request.params_hash == "params-hash"
    assert request.rulepack_hash == "rulepack-hash"
    assert request.resource_hint == "cpu"
    assert request.doc_affinity_key == "affinity-key"


def test_qc_event_maps_page_report_to_event() -> None:
    """PageReport 是质检器 → 调度器的边界消息：pass 推进、fail/uncertain 回退。"""
    pass_report = PageReport(
        doc_id="sha256:doc",
        page_no=1,
        verdict=Verdict.PASS,
        extract_quality=0.9,
        qc_confidence=0.9,
    )
    assert qc_event(pass_report) is Event.QC_PASS

    for verdict in (Verdict.FAIL, Verdict.UNCERTAIN):
        report = PageReport(
            doc_id="sha256:doc",
            page_no=1,
            verdict=verdict,
            extract_quality=0.4,
            qc_confidence=0.6,
        )
        assert qc_event(report) is Event.QC_FAIL
