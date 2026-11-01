"""心跳监控：按「进展」而非「耗时」判定存活（research D7）。

worker 每完成一个批即上报一次进展心跳；同值进展不续命，连续
`timeout_s`（默认 60s）没有进展心跳即判定死亡。耗时再长，只要进展在推进就存活。
"""

from __future__ import annotations

import time
from collections.abc import Callable

DEFAULT_HEARTBEAT_TIMEOUT_S = 60.0


class HeartbeatMonitor:
    """记录每个任务的最后进展与时间戳，供控制面判定死亡并重新入队。"""

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s 必须 > 0")
        self._timeout_s = timeout_s
        self._clock = clock
        self._progress: dict[str, int] = {}
        self._last_at: dict[str, float] = {}

    def beat(self, task_id: str, progress: int) -> bool:
        """上报进展心跳；仅在进展前进时记录（返回 True），同值 / 回退不算进展。"""
        if progress < 0:
            raise ValueError("progress 必须 ≥ 0")
        last = self._progress.get(task_id)
        if last is not None and progress <= last:
            return False
        self._progress[task_id] = progress
        self._last_at[task_id] = self._clock()
        return True

    def last_progress(self, task_id: str) -> int | None:
        return self._progress.get(task_id)

    def is_alive(self, task_id: str) -> bool:
        """存在进展记录且距上次进展不超过 timeout 即存活。"""
        last_at = self._last_at.get(task_id)
        if last_at is None:
            return False
        return self._clock() - last_at <= self._timeout_s

    def dead_tasks(self) -> tuple[str, ...]:
        """全部已判死的任务（控制面据此重新入队）。"""
        return tuple(task_id for task_id in self._last_at if not self.is_alive(task_id))
