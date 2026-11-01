"""ReplanPolicy 模型实现占位（M3）。"""

from __future__ import annotations

from typing import Any

from dataagent.contracts.policies import Plan


class ModelReplanPolicy:
    def decide(self, exec_stats: dict[str, Any]) -> Plan | None:
        raise NotImplementedError("ReplanPolicy 模型实现属于 M3")
