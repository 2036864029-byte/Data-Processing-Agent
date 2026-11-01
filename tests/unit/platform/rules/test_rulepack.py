"""规则包测试（T115，B8）——FR-014。

YAML 纯数据、schema 校验、冲突发布阻断、回滚、dry-run；
每条规则可追溯原始需求文本（FR-016 编译链路属 P1，P0 只做规则包本身）。
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from dataagent.platform.rules.rulepack import (
    RulePack,
    RulePackConflictError,
    RulePackRegistry,
    dry_run,
    load_rulepack,
)


def pack_dict(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "rulepack": "cn_a_share_annual_v1",
        "version": "1.0.0",
        "fields": [
            {
                "id": "revenue",
                "label": "营业收入",
                "type": "money",
                "anchors": ["营业收入"],
                "required": True,
            },
            {
                "id": "cost",
                "label": "营业成本",
                "type": "money",
                "anchors": ["营业成本"],
            },
        ],
        "checks": [
            {
                "id": "positive_revenue",
                "type": "expression",
                "expr": {">": [{"var": "revenue"}, 0]},
                "requirement": "营业收入必须为正数",
            },
            {
                "id": "page_number_sequence",
                "type": "sequence",
                "requirement": "页码必须连续",
            },
        ],
        "thresholds": {"extract_quality_min": 0.85},
    }
    data.update(overrides)
    return data


def make_pack(**overrides: object) -> RulePack:
    return RulePack.model_validate(pack_dict(**overrides))


def test_valid_pack_is_accepted() -> None:
    pack = make_pack()

    assert pack.rulepack == "cn_a_share_annual_v1"
    assert [field.id for field in pack.fields] == ["revenue", "cost"]
    assert [check.id for check in pack.checks] == [
        "positive_revenue",
        "page_number_sequence",
    ]


def test_requirement_text_is_mandatory() -> None:
    """traceability：每条规则必须可追溯到原始需求文本。"""
    checks = [
        {
            "id": "positive_revenue",
            "type": "expression",
            "expr": {">": [{"var": "revenue"}, 0]},
        }
    ]

    with pytest.raises(ValidationError):
        make_pack(checks=checks)


def test_expression_must_reference_declared_fields() -> None:
    checks = [
        {
            "id": "bad_ref",
            "type": "expression",
            "expr": {"==": [{"var": "not_a_field"}, 1]},
            "requirement": "引用未声明字段",
        }
    ]

    with pytest.raises(ValidationError):
        make_pack(checks=checks)


def test_expression_check_requires_expr() -> None:
    checks = [
        {"id": "missing_expr", "type": "expression", "requirement": "缺表达式"}
    ]

    with pytest.raises(ValidationError):
        make_pack(checks=checks)


def test_sequence_check_forbids_expr() -> None:
    checks = [
        {
            "id": "seq",
            "type": "sequence",
            "expr": {"==": [1, 1]},
            "requirement": "序列检查",
        }
    ]

    with pytest.raises(ValidationError):
        make_pack(checks=checks)


def test_duplicate_check_ids_are_rejected() -> None:
    check = {
        "id": "dup",
        "type": "expression",
        "expr": {">": [{"var": "revenue"}, 0]},
        "requirement": "重复 id",
    }

    with pytest.raises(ValidationError):
        make_pack(checks=[check, check])


def test_invalid_regex_is_rejected() -> None:
    fields = [
        {
            "id": "revenue",
            "label": "营业收入",
            "type": "money",
            "anchors": ["营业收入"],
            "pattern": "([",
        }
    ]

    with pytest.raises(ValidationError):
        make_pack(fields=fields)


def test_publish_activates_pack() -> None:
    registry = RulePackRegistry()
    pack = make_pack()

    registry.publish(pack)

    assert registry.active(pack.rulepack) == pack


def test_conflicting_publication_is_blocked() -> None:
    registry = RulePackRegistry()
    registry.publish(make_pack())

    with pytest.raises(RulePackConflictError):
        registry.publish(make_pack())

    with pytest.raises(RulePackConflictError):
        registry.publish(make_pack(version="0.9.0"))


def test_new_version_supersedes_previous() -> None:
    registry = RulePackRegistry()
    registry.publish(make_pack(version="1.0.0"))
    newer = make_pack(version="1.1.0")

    registry.publish(newer)

    assert registry.active(newer.rulepack) == newer
    assert registry.versions(newer.rulepack) == ["1.0.0", "1.1.0"]


def test_rollback_restores_previous_version() -> None:
    registry = RulePackRegistry()
    first = make_pack(version="1.0.0")
    registry.publish(first)
    registry.publish(make_pack(version="1.1.0"))

    restored = registry.rollback(first.rulepack, "1.0.0")

    assert restored == first
    assert registry.active(first.rulepack) == first


def test_dry_run_reports_each_check_with_requirement() -> None:
    pack = make_pack()

    report = dry_run(pack, [{"revenue": 10}, {"revenue": -1}])

    first, second = report.samples
    positive_first = next(
        result for result in first.results if result.check_id == "positive_revenue"
    )
    positive_second = next(
        result for result in second.results if result.check_id == "positive_revenue"
    )
    sequence = next(
        result for result in first.results if result.check_id == "page_number_sequence"
    )

    assert positive_first.passed is True
    assert positive_second.passed is False
    assert positive_first.requirement == "营业收入必须为正数"
    assert sequence.passed is None
    assert report.failed == 1
    assert report.skipped == 2


def test_load_rulepack_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "rulepack.yaml"
    path.write_text(
        yaml.safe_dump(pack_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    pack = load_rulepack(path)

    assert pack.rulepack == "cn_a_share_annual_v1"
    assert pack.checks[0].requirement == "营业收入必须为正数"
