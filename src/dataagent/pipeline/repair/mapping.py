"""缺陷码 → 算子映射（写死）。

宪法 G2 / G5 + defect-codes.md「两张映射表」：defect_code → operator 是代码、写死、固定，
输出行为必须可复现，禁运行时推断——同一缺陷两次修复不得走不同算子。
第 10/11 号缺陷码不设修复算子（文档级标记 / 人工），映射到 None。

映射从契约规格表（contracts/defects.DEFECT_SPECS）派生，避免两份映射漂移；
新增缺陷码必须同步 DEFECT_SPECS（G9：枚举与映射长度一致，test_mapping.py 强制）。
"""

from dataagent.contracts.defects import DEFECT_SPECS, RepairOperator
from dataagent.contracts.errors import DefectCode

# defect_code → 默认算子（写死；无修复算子的码映射到 None）
DEFECT_OPERATOR_MAPPING: dict[DefectCode, RepairOperator | None] = {
    code: spec.operator for code, spec in DEFECT_SPECS.items()
}


def operator_for(code: DefectCode) -> RepairOperator | None:
    """返回该缺陷码的固定算子；无修复算子（第 10/11 号）返回 None。"""
    return DEFECT_OPERATOR_MAPPING[code]
