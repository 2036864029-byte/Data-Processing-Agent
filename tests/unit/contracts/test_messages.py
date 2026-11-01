"""角色消息契约测试（T043 依赖，contracts/role-messages.md）。

ParseRequest / PageReport 字段与必填约束；OperatorResult 复用 contracts/defects.py。
"""

import pytest
from pydantic import ValidationError

from dataagent.contracts.messages import PageReport, ParseRequest, Verdict

VALID_PARSE_REQUEST: dict[str, object] = {
    "task_id": "uuidv7-task",
    "doc_id": "sha256-doc",
    "page_range": (1, 33),
    "engine": "engine.pdf@1.0.0",
    "params_hash": "sha256-params",
    "rulepack_hash": "sha256-rulepack",
    "resource_hint": "cpu",
    "doc_affinity_key": "sha256-affinity",
}


def test_parse_request_valid() -> None:
    request = ParseRequest.model_validate(VALID_PARSE_REQUEST)
    assert request.page_range == (1, 33)
    assert request.engine == "engine.pdf@1.0.0"


@pytest.mark.parametrize("page_range", [(0, 5), (5, 3), (1,)])
def test_parse_request_page_range_must_be_1_indexed(page_range: tuple[int, ...]) -> None:
    """page_range 为 1-indexed 闭区间，且 start ≤ end。"""
    with pytest.raises(ValidationError):
        ParseRequest.model_validate({**VALID_PARSE_REQUEST, "page_range": page_range})


def test_parse_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ParseRequest.model_validate({**VALID_PARSE_REQUEST, "unexpected": 1})


def test_page_report_valid_with_defect() -> None:
    report = PageReport.model_validate(
        {
            "doc_id": "sha256-doc",
            "page_no": 3,
            "verdict": "fail",
            "extract_quality": 0.42,
            "defects": [
                {"code": "table_structure_broken", "location": {"page_no": 3}}
            ],
            "attribution": "table_structure",
            "recommended_action": "rebuild_table_cells",
            "qc_confidence": 0.88,
        }
    )
    assert report.verdict is Verdict.FAIL
    assert report.defects[0].location.page_no == 3
    assert report.recommended_action == "rebuild_table_cells"


def test_page_report_rejects_invalid_page_and_quality() -> None:
    base = {
        "doc_id": "sha256-doc",
        "page_no": 1,
        "verdict": "pass",
        "extract_quality": 0.9,
        "qc_confidence": 0.9,
    }
    with pytest.raises(ValidationError):
        PageReport.model_validate({**base, "page_no": 0})
    with pytest.raises(ValidationError):
        PageReport.model_validate({**base, "extract_quality": 1.5})
    with pytest.raises(ValidationError):
        PageReport.model_validate({**base, "verdict": "maybe"})
