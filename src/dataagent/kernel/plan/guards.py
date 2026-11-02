"""Plan 守卫：模型调用 / 重规划 / 墙钟时间三项硬上限。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from dataagent.kernel.plan.schemas import PlanGuards


@dataclass(slots=True)
class PlanGuardTracker:
    """guards 计数器；跨重规划共享同一实例（模型调用 / 重规划 / 时间累计）。"""

    guards: PlanGuards
    clock: Callable[[], float] = time.monotonic
    _started_at: float = field(init=False)
    _model_calls: int = field(default=0, init=False)
    _replans: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._started_at = self.clock()

    @property
    def model_calls(self) -> int:
        return self._model_calls

    @property
    def replans(self) -> int:
        return self._replans

    @property
    def elapsed_s(self) -> float:
        return self.clock() - self._started_at

    def record_model_call(self, count: int = 1) -> None:
        self._model_calls += count

    def register_replan(self) -> None:
        self._replans += 1

    def stop_reason(self) -> str | None:
        """返回触发的 guard 字段名；无触发返回 None。"""
        if self._model_calls > self.guards.max_model_calls:
            return "max_model_calls"
        if self._replans > self.guards.max_replans:
            return "max_replans"
        if self.elapsed_s > self.guards.max_wall_time_s:
            return "max_wall_time_s"
        return None
