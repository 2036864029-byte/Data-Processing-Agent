"""观测事件测试（Phase 6）：page_gap / heading_decision / normalization_applied。"""

from __future__ import annotations

from collections.abc import Callable

from dataagent.contracts.defects import Defect, DefectLocation
from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.events import log_page_gap, primary_metric
from dataagent.pipeline.quality.normalization import normalize_text_with_kinds
from dataagent.pipeline.structure.headings import HeadingLevelNormalizer

ReadEvents = Callable[[], list[dict[str, object]]]


def defect(
    code: DefectCode, evidence: dict[str, float | int | str]
) -> Defect:
    return Defect(
        code=code,
        location=DefectLocation(page_no=2),
        evidence=evidence,
    )


def test_primary_metric_picks_diagnostic_value_and_threshold() -> None:
    metric, value, threshold = primary_metric(
        [defect(DefectCode.LOW_DENSITY, {"char_density": 0.31, "threshold": 0.9})]
    )

    assert (metric, value, threshold) == ("char_density", 0.31, 0.9)


def test_primary_metric_ignores_strings_and_missing_threshold() -> None:
    metric, value, threshold = primary_metric(
        [defect(DefectCode.TABLE_STRUCTURE_BROKEN, {"table_header_ok": "false"})]
    )

    assert (metric, value, threshold) == (None, None, None)


def test_page_gap_event_carries_page_context(
    captured_events: ReadEvents,
) -> None:
    log_page_gap(
        doc_id="d" * 8,
        page_no=7,
        codes=["low_density"],
        defects=[
            defect(DefectCode.LOW_DENSITY, {"char_density": 0.2, "threshold": 0.9})
        ],
        page_type="mixed",
        text_layer="present",
        source="quality",
        fallback="mark_only",
        tier="T1",
    )

    [event] = captured_events()

    assert event["event"] == "page_gap"
    assert event["page_no"] == 7
    assert event["defect_codes"] == ["low_density"]
    assert event["metric"] == "char_density"
    assert event["value"] == 0.2
    assert event["threshold"] == 0.9
    assert event["page_type"] == "mixed"
    assert event["text_layer"] == "present"
    assert event["source"] == "quality"


def test_normalization_reports_kinds() -> None:
    assert normalize_text_with_kinds("中 文")[1] == frozenset({"spaces"})
    assert normalize_text_with_kinds("2（）")[1] == frozenset({"numbering"})
    assert normalize_text_with_kinds("第一行\n第二行")[1] == frozenset({"reflow"})
    assert normalize_text_with_kinds("已经规范。")[1] == frozenset()


def test_heading_decision_logs_promotion_and_clamp(
    captured_events: ReadEvents,
) -> None:
    normalizer = HeadingLevelNormalizer()

    normalizer.resolve("第一章 总则", None, page_no=1)
    normalizer.resolve("五级标题", 5, page_no=2)

    events = [
        event
        for event in captured_events()
        if event["event"] == "heading_decision"
    ]
    assert [(event["page_no"], event["source"], event["reason"], event["level"])
            for event in events] == [
        (1, "numbering", "promoted", 1),
        (2, "clamped", "jump_clamped", 2),
    ]


def test_heading_decision_is_silent_when_declared_level_is_kept(
    captured_events: ReadEvents,
) -> None:
    HeadingLevelNormalizer().resolve("普通标题", 2, page_no=1)

    assert captured_events() == []
