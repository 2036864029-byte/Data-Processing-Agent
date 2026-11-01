"""StrategyPolicy 模型实现占位（M3）。"""

from __future__ import annotations

from typing import Any

from dataagent.contracts.policies import Strategy


class ModelStrategyPolicy:
    def select(self, task_stats: dict[str, Any]) -> Strategy:
        raise NotImplementedError("StrategyPolicy 模型实现属于 M3")
