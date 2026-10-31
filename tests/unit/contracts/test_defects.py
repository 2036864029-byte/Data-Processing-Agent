"""缺陷契约测试（T018，A3）。

11 码封闭枚举（逐字引用 defect-codes.md）；location 必填、缺 location 不进修复队列；
raw_reason 仅 code = plugin_reason_unknown 时必填（data-model.md Defect 规则 3）。
"""

import pytest
from pydantic import ValidationError

from dataagent.contracts.defects import (
    DEFECT_SPECS,
    Defect,
    OperatorResult,
    RepairOperator,
)
from dataagent.contracts.errors import DefectCode

EXPECTED_CODES = [
    "encoding_loss",
    "low_density",
    "region_empty",
    "table_structure_broken",
    "table_cross_page_break",
    "reading_order_broken",
    "column_mixed",
    "header_footer_leak",
    "page_rotated",
    "page_number_broken",
    "plugin_reason_unknown",
]


def _make_defect(**overrides: object) -> Defect:
    defaults: dict[str, object] = {
        "code": DefectCode.ENCODING_LOSS,
        "location": {"page_no": 3},
        "severity": 0.8,
    }
    defaults.update(overrides)
    return Defect(**defaults)  # type: ignore[arg-type]


def test_defect_code_enum_closed() -> None:
    """11 码封闭枚举：与 defect-codes.md 逐字一致、无重复。"""
    assert len(DefectCode) == 11
    assert {code.value for code in DefectCode} == set(EXPECTED_CODES)


def test_defect_specs_cover_all_codes() -> None:
    """11 码规格表覆盖全部缺陷码，与枚举长度一致（G9：新增码必须同步）。"""
    assert len(DEFECT_SPECS) == len(DefectCode)
    assert set(DEFECT_SPECS) == set(DefectCode)


def test_no_operator_codes_are_document_level() -> None:
    """第 10/11 项不设修复算子（文档级标记 / 人工），规格表 operator 为 None、档位为空。"""
    assert DEFECT_SPECS[DefectCode.PAGE_NUMBER_BROKEN].operator is None
    assert DEFECT_SPECS[DefectCode.PLUGIN_REASON_UNKNOWN].operator is None
    assert DEFECT_SPECS[DefectCode.PAGE_NUMBER_BROKEN].tier == ()


def test_primary_operator_matches_contract() -> None:
    """默认算子与 defect-codes.md「默认算子」列首项一致（低密度 / 旋转取主档算子）。"""
    assert DEFECT_SPECS[DefectCode.ENCODING_LOSS].operator == RepairOperator.OCR_PAGE
    assert DEFECT_SPECS[DefectCode.LOW_DENSITY].operator == RepairOperator.OCR_PAGE
    assert DEFECT_SPECS[DefectCode.PAGE_ROTATED].operator == RepairOperator.ROTATE_PAGE


def test_location_required() -> None:
    """location 必填：缺 location 无法构造缺陷（不进修复队列，只能统计）。"""
    with pytest.raises(ValidationError):
        Defect(code=DefectCode.ENCODING_LOSS, severity=0.8)


def test_location_page_no_must_be_1_indexed() -> None:
    """location.page_no 必填且 ≥ 1（内部一律 1-indexed）。"""
    with pytest.raises(ValidationError):
        Defect(code=DefectCode.ENCODING_LOSS, location={}, severity=0.8)
    with pytest.raises(ValidationError):
        Defect(code=DefectCode.ENCODING_LOSS, location={"page_no": 0}, severity=0.8)


def test_raw_reason_required_only_for_unknown() -> None:
    """raw_reason 仅 code = plugin_reason_unknown 时必填。"""
    with pytest.raises(ValidationError):
        Defect(
            code=DefectCode.PLUGIN_REASON_UNKNOWN,
            location={"page_no": 2},
            severity=0.5,
        )
    d = _make_defect(
        code=DefectCode.PLUGIN_REASON_UNKNOWN,
        location={"page_no": 2},
        raw_reason="unknown_reason_xyz",
    )
    assert d.raw_reason == "unknown_reason_xyz"  # 上游原因原样保留


def test_raw_reason_optional_for_known_codes() -> None:
    assert _make_defect().raw_reason is None


def test_operator_result_changed_is_stop_flag() -> None:
    """OperatorResult.changed == false 是停机标志（宪法 G5）。"""
    r = OperatorResult(doc_id="sha256:doc", page_no=1, changed=False)
    assert r.changed is False
    assert r.candidate_artifact_id is None
