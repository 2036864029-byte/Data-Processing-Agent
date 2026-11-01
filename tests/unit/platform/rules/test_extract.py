"""声明式字段提取测试（T119，B8）——PRD 4.6.6 第①层。

锚点词 → 定位 → 抽值 → 类型校验；缺失必填字段显式标记，不静默。
"""

from dataagent.platform.rules.extract import extract_declarative_fields
from dataagent.platform.rules.rulepack import RulePack


def pack(**overrides: object) -> RulePack:
    data: dict[str, object] = {
        "rulepack": "demo_v1",
        "version": "1.0.0",
        "fields": [
            {
                "id": "revenue",
                "label": "营业收入",
                "type": "money",
                "anchors": ["营业收入", "营业总收入"],
                "required": True,
            },
            {
                "id": "report_date",
                "label": "报告日期",
                "type": "date",
                "anchors": ["报告日期"],
            },
        ],
        "checks": [],
        "thresholds": {},
    }
    data.update(overrides)
    return RulePack.model_validate(data)


def test_same_line_value_is_extracted_and_typed() -> None:
    outcome = extract_declarative_fields(
        pack(), "营业收入：1,234.50\n报告日期：2026-03-31\n"
    )

    values = {field.field_id: field.value for field in outcome.fields}
    assert values["revenue"] == 1234.5
    assert values["report_date"] == "2026-03-31"
    assert outcome.missing_required == ()


def test_value_on_next_line_is_extracted() -> None:
    outcome = extract_declarative_fields(pack(), "营业收入\n5,000\n报告日期：2026-03-31\n")

    revenue = next(field for field in outcome.fields if field.field_id == "revenue")
    assert revenue.value == 5000.0
    assert revenue.anchor == "营业收入"


def test_anchor_aliases_are_tried_in_order() -> None:
    outcome = extract_declarative_fields(
        pack(), "营业总收入：888\n报告日期：2026-03-31\n"
    )

    revenue = next(field for field in outcome.fields if field.field_id == "revenue")
    assert revenue.value == 888.0
    assert revenue.anchor == "营业总收入"


def test_missing_required_field_is_flagged() -> None:
    outcome = extract_declarative_fields(pack(), "报告日期：2026-03-31\n")

    revenue = next(field for field in outcome.fields if field.field_id == "revenue")
    assert revenue.missing is True
    assert revenue.value is None
    assert outcome.missing_required == ("revenue",)


def test_regex_pattern_is_applied() -> None:
    fields = [
        {
            "id": "order_no",
            "label": "订单号",
            "type": "text",
            "anchors": ["订单号"],
            "pattern": r"([A-Z]{2}\d{4})",
        }
    ]
    outcome = extract_declarative_fields(
        pack(fields=fields), "订单号：AB1234\n"
    )

    assert outcome.fields[0].value == "AB1234"
    assert outcome.fields[0].source == "declarative"


def test_extraction_is_deterministic() -> None:
    text = "营业收入：100\n报告日期：2026-03-31\n"

    assert extract_declarative_fields(pack(), text) == extract_declarative_fields(
        pack(), text
    )
