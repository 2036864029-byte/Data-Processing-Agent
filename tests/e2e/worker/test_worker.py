"""worker 进程壳测试（T046，A7）。

worker 无状态（任务归属靠队列）、从队列取批、心跳按进展；`apps/worker` 内禁 asyncio.run。
"""

from dataclasses import dataclass, field
from pathlib import Path

import dataagent.apps.worker as worker_package
from dataagent.apps.worker.worker import Worker, WorkerConfig
from dataagent.contracts.messages import ParseRequest
from dataagent.platform.runtime.heartbeat import HeartbeatMonitor
from dataagent.platform.store.queue.base import Queue, ReceivedMessage


@dataclass
class FakeQueue(Queue):
    messages: list[ReceivedMessage] = field(default_factory=list)
    acked: list[str] = field(default_factory=list)
    dead: list[str] = field(default_factory=list)

    def enqueue(self, payload: bytes) -> str:
        message_id = str(len(self.messages) + 1)
        self.messages.append(ReceivedMessage(id=message_id, payload=payload))
        return message_id

    def receive(self, max_count: int = 1) -> list[ReceivedMessage]:
        batch, self.messages = self.messages[:max_count], self.messages[max_count:]
        return batch

    def ack(self, message_id: str) -> None:
        self.acked.append(message_id)

    def dead_letter(self, message_id: str) -> None:
        self.dead.append(message_id)


class RecordingHandler:
    def __init__(self) -> None:
        self.requests: list[ParseRequest] = []

    def handle(self, request: ParseRequest) -> None:
        self.requests.append(request)


class FailingHandler:
    def handle(self, request: ParseRequest) -> None:
        raise RuntimeError("pipeline exploded")


def make_message(
    *, task_id: str = "task-1", page_range: tuple[int, int] = (33, 64), message_id: str = "1"
) -> ReceivedMessage:
    request = ParseRequest(
        task_id=task_id,
        doc_id="sha256:doc",
        page_range=page_range,
        engine="engine.pdf@1.0.0",
        params_hash="params",
        rulepack_hash="rulepack",
        resource_hint="cpu",
        doc_affinity_key="affinity",
    )
    return ReceivedMessage(id=message_id, payload=request.model_dump_json().encode())


def make_worker(
    queue: FakeQueue,
    handler: object,
    monitor: HeartbeatMonitor,
    *,
    max_messages: int = 1,
) -> Worker:
    return Worker(
        queue=queue,
        handler=handler,  # type: ignore[arg-type]
        heartbeat=monitor,
        config=WorkerConfig(max_messages=max_messages),
    )


def test_worker_pulls_batch_acks_and_beats_progress() -> None:
    queue = FakeQueue(messages=[make_message()])
    handler = RecordingHandler()
    monitor = HeartbeatMonitor(clock=lambda: 0.0)
    worker = make_worker(queue, handler, monitor)

    assert worker.run_once() == 1
    assert handler.requests[0].page_range == (33, 64)
    assert queue.acked == ["1"]
    assert queue.dead == []
    assert monitor.last_progress("task-1") == 64  # 按页进展，而非耗时


def test_worker_has_no_task_state_fields() -> None:
    """worker 无状态：不持有任务归属，任务归属靠队列。"""
    worker = make_worker(
        FakeQueue(), RecordingHandler(), HeartbeatMonitor(clock=lambda: 0.0)
    )
    assert not hasattr(worker, "tasks")
    assert not hasattr(worker, "task_id")


def test_two_workers_share_queue_as_only_state() -> None:
    queue = FakeQueue(
        messages=[
            make_message(message_id="1", page_range=(1, 32)),
            make_message(message_id="2", page_range=(33, 64)),
        ]
    )
    monitor = HeartbeatMonitor(clock=lambda: 0.0)
    first = make_worker(queue, RecordingHandler(), monitor)
    second = make_worker(queue, RecordingHandler(), monitor)

    assert first.run_once() == 1
    assert second.run_once() == 1
    assert queue.acked == ["1", "2"]
    assert queue.messages == []


def test_handler_failure_goes_to_dead_letter_without_ack_or_progress() -> None:
    queue = FakeQueue(messages=[make_message()])
    monitor = HeartbeatMonitor(clock=lambda: 0.0)
    worker = make_worker(queue, FailingHandler(), monitor)

    assert worker.run_once() == 1
    assert queue.dead == ["1"]
    assert queue.acked == []
    assert monitor.last_progress("task-1") is None


def test_malformed_payload_goes_to_dead_letter() -> None:
    queue = FakeQueue(messages=[ReceivedMessage(id="bad", payload=b"not-json")])
    monitor = HeartbeatMonitor(clock=lambda: 0.0)
    worker = make_worker(queue, RecordingHandler(), monitor)

    assert worker.run_once() == 1
    assert queue.dead == ["bad"]
    assert queue.acked == []


def test_config_batch_size_consumes_multiple_messages() -> None:
    queue = FakeQueue(
        messages=[
            make_message(message_id="1", page_range=(1, 32)),
            make_message(message_id="2", page_range=(33, 64)),
        ]
    )
    handler = RecordingHandler()
    monitor = HeartbeatMonitor(clock=lambda: 0.0)
    worker = make_worker(queue, handler, monitor, max_messages=2)

    assert worker.run_once() == 2
    assert len(handler.requests) == 2
    assert queue.acked == ["1", "2"]
    assert monitor.last_progress("task-1") == 64


def test_worker_package_has_no_asyncio_run() -> None:
    """apps/worker 内禁 asyncio.run（宪法 IV，grep 门禁 G9 的测试侧补充）。"""
    package_dir = Path(worker_package.__file__).parent
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in package_dir.glob("*.py")
    )
    assert "asyncio.run" not in source
