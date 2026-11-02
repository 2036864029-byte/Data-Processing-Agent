"""任务控制面：唯一状态机推进、进度写入与任务级结束处理。

只做控制面协调，不读写文档产物。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from dataagent.contracts.messages import ParseRequest
from dataagent.kernel.state import Event, State
from dataagent.kernel.supervisor import BatchOutcome, Supervisor
from dataagent.pipeline.telemetry import TaskTelemetry
from dataagent.platform.observe.alerts import Alert
from dataagent.platform.observe.log import log_event
from dataagent.platform.store.sql.tasks import TaskProgress, TaskProgressStore


class NoopBatchExecutor:
    """runner 自身执行解析，supervisor 仅用状态机时使用。"""

    def execute(self, request: ParseRequest) -> BatchOutcome:
        del request
        return BatchOutcome(artifact_ids=())


class NoopArtifactWriter:
    def write(self, artifact_id: str) -> None:
        del artifact_id


class TaskControl:
    def __init__(
        self,
        *,
        supervisor: Supervisor,
        telemetry: TaskTelemetry,
        progress: TaskProgressStore | None,
    ) -> None:
        self._supervisor = supervisor
        self._telemetry = telemetry
        self._progress = progress
        self._task_id = "task-local"
        self._state = State.RECEIVED

    @property
    def state(self) -> State:
        return self._state

    @property
    def task_id(self) -> str:
        return self._task_id

    def begin(self, task_id: str) -> State:
        self._task_id = task_id
        self._telemetry.reset()
        self._state = self._supervisor.begin_task(task_id)
        return self._state

    def advance(self, event: Event) -> State:
        self._state = self._supervisor.advance(self._task_id, event)
        log_event(
            "state_transition",
            task_id=self._task_id,
            state=self._state.value,
            transition_event=event.value,
        )
        return self._state

    def completion_events(
        self, *, processed_docs: int, failed_docs: int, gap_pages: int
    ) -> list[Event]:
        """任务级状态投影：页级细节收敛为一条合法路径。"""
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

    def record_progress(
        self,
        state: str,
        *,
        done: int,
        total: int,
        detail: Mapping[str, Any],
    ) -> None:
        """进度 best-effort 写入：失败只记日志，不影响主链路。"""
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

    def finish(
        self, extra: Mapping[str, float | int] | None = None
    ) -> tuple[Alert, ...]:
        return self._telemetry.finish(self._task_id, extra=extra)
