"""worker 进程壳：无状态，从队列取批 → 处理 → 心跳 → ACK。

宪法 VI：worker 无状态，任务归属靠队列；死了换一个接着干。P0-A 为空壳循环，
真实 pipeline / kernel.policies 在 P0-B 接入。
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from typing import Protocol

from dataagent.contracts.messages import ParseRequest
from dataagent.platform.runtime.heartbeat import HeartbeatMonitor
from dataagent.platform.store.queue.base import Queue


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    max_messages: int = 1
    poll_interval_s: float = 1.0


DEFAULT_WORKER_CONFIG = WorkerConfig()


class BatchHandler(Protocol):
    """批处理回调（P0-B 接入 pipeline + kernel.policies）。"""

    def handle(self, request: ParseRequest) -> None: ...


class ShellBatchHandler:
    """P0-A 空壳：真实阶段 P0-B 接入。"""

    def handle(self, request: ParseRequest) -> None:
        return None


class Worker:
    """无状态 worker：唯一外部状态是队列消息本身。"""

    def __init__(
        self,
        *,
        queue: Queue,
        handler: BatchHandler,
        heartbeat: HeartbeatMonitor,
        config: WorkerConfig = DEFAULT_WORKER_CONFIG,
    ) -> None:
        if config.max_messages < 1:
            raise ValueError("max_messages 必须 ≥ 1")
        self._queue = queue
        self._handler = handler
        self._heartbeat = heartbeat
        self._config = config

    def run_once(self) -> int:
        """取一批消息；成功处理 → 心跳（按页进展）+ ACK，失败 → 死信。"""
        messages = self._queue.receive(self._config.max_messages)
        for message in messages:
            try:
                request = ParseRequest.model_validate_json(message.payload)
                self._handler.handle(request)
            except Exception:
                self._queue.dead_letter(message.id)
                continue
            self._heartbeat.beat(request.task_id, request.page_range[1])
            self._queue.ack(message.id)
        return len(messages)

    def run_forever(self, stop: threading.Event) -> None:
        """空壳循环：队列空则按 poll_interval 等待；后端暂不可用则告警后重试。"""
        while not stop.is_set():
            try:
                processed = self.run_once()
            except Exception as exc:
                print(
                    f"worker: 队列暂不可用（{exc}），{self._config.poll_interval_s}s 后重试",
                    file=sys.stderr,
                    flush=True,
                )
                stop.wait(self._config.poll_interval_s)
                continue
            if processed == 0:
                stop.wait(self._config.poll_interval_s)
