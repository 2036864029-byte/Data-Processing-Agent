"""OperatorPolicy 规则实现：Defect → 固定算子（禁运行时推断）。

映射与 `pipeline/repair/mapping.py` 同源：二者都派生自 contracts/defects.py 的
DEFECT_SPECS；kernel 不得反向依赖 pipeline（import-linter 分层），故此处直接读契约表。
`Defect.suggested_action` 仅供报告，不参与算子选择。
第 10/11 号缺陷码无修复算子（记录 + 计数，交人工），显式报错而不是猜一个。
"""

from __future__ import annotations

from dataagent.contracts.defects import DEFECT_SPECS, Defect, RepairOperator


class NoRepairOperatorError(ValueError):
    """该缺陷码不设修复算子（第 10/11 号：文档级标记 / 人工）。"""


class RuleOperatorPolicy:
    def operator(self, defect: Defect) -> RepairOperator:
        operator = DEFECT_SPECS[defect.code].operator
        if operator is None:
            raise NoRepairOperatorError(
                f"缺陷码 {defect.code.value} 无修复算子（记录 + 计数，交人工）"
            )
        return operator
