"""PlanPolicy 模型实现占位（M3）。

P0 使用规则模板；本文件族是唯一允许引入模型 SDK 的位置（宪法门禁）。
"""

from __future__ import annotations

from typing import Any

from dataagent.contracts.policies import Plan


class ModelPlanPolicy:
    def compile(self, nl: str, task_stats: dict[str, Any]) -> Plan:
        raise NotImplementedError("PlanPolicy 模型实现属于 M3")
