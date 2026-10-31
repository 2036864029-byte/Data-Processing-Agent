"""缺陷码 → 算子映射测试（T018，A3）。

mapping.py 覆盖全部 11 码且枚举与映射长度一致（G9 grep 门禁）；
写死、单向、禁运行时推断——同一缺陷两次修复不得走不同算子。
"""

from dataagent.contracts.defects import DEFECT_SPECS, RepairOperator
from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.repair.mapping import DEFECT_OPERATOR_MAPPING, operator_for


def test_mapping_covers_all_defect_codes() -> None:
    """映射覆盖全部 11 个缺陷码，且枚举与映射长度一致（G9）。"""
    assert len(DEFECT_OPERATOR_MAPPING) == len(DefectCode)
    assert set(DEFECT_OPERATOR_MAPPING) == set(DefectCode)


def test_mapping_consistent_with_specs() -> None:
    """映射与契约规格表一致：operator_for(code) == DEFECT_SPECS[code].operator。"""
    for code, spec in DEFECT_SPECS.items():
        assert operator_for(code) is spec.operator


def test_mapping_written_in_code_deterministic() -> None:
    """写死：每个缺陷码恰好一个固定算子，同一缺陷两次修复走同一算子。"""
    for code in DefectCode:
        first = operator_for(code)
        second = operator_for(code)
        assert first is second
        assert first is DEFECT_OPERATOR_MAPPING[code]


def test_known_code_maps_to_fixed_operator() -> None:
    """逐字引用 defect-codes.md：encoding_loss → ocr_page、表格结构 → rebuild_table_cells。"""
    assert operator_for(DefectCode.ENCODING_LOSS) == RepairOperator.OCR_PAGE
    assert operator_for(DefectCode.TABLE_STRUCTURE_BROKEN) == RepairOperator.REBUILD_TABLE_CELLS


def test_no_operator_codes_map_to_none() -> None:
    """第 10/11 项不设修复算子（文档级标记 / 人工），映射为 None。"""
    assert DEFECT_OPERATOR_MAPPING[DefectCode.PAGE_NUMBER_BROKEN] is None
    assert DEFECT_OPERATOR_MAPPING[DefectCode.PLUGIN_REASON_UNKNOWN] is None
