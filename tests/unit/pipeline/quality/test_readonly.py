"""QC 只读与落库顺序测试（T095，B5）——G5 / role-messages 硬性约束 #2。

质检器不重新解析、不修改产物；质检结果先落库再触发下游；
缺陷必带可操作 location，无算子（仅统计）的缺陷不进入修复队列。
"""

import dataclasses

import pytest

from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.quality.gate import (
    InMemoryQualityResultStore,
    QualityGate,
)
from dataagent.pipeline.quality.l1_check import (
    L1_CHECKER,
    L1Metrics,
    L1PageInput,
    defects_for_repair,
)

DOC_ID = "d" * 64


def page_input(**overrides: object) -> L1PageInput:
    metrics = L1Metrics(
        char_density=8.0,
        glyph_coverage=0.99,
        cid_unmapped_ratio=0.001,
        empty_region_ratio=0.05,
        reading_order_score=0.97,
        header_footer_leak=0.01,
        page_number_ok=True,
    )
    values: dict[str, object] = {
        "doc_id": DOC_ID,
        "page_no": 9,
        "metrics": metrics,
    }
    values.update(overrides)
    return L1PageInput(**values)  # type: ignore[arg-type]


def test_quality_result_is_persisted_before_downstream() -> None:
    """先落库再触发下游：崩溃后可从库判断该页是否判过。"""
    events: list[str] = []

    class SpyStore(InMemoryQualityResultStore):
        def save(self, report) -> None:  # type: ignore[no-untyped-def]
            events.append("saved")
            super().save(report)

    gate = QualityGate(checker=L1_CHECKER, store=SpyStore())
    page = page_input(metrics=L1Metrics(glyph_coverage=0.5, char_density=8.0))

    gate.run_page(page, on_saved=lambda report: events.append("downstream"))

    assert events == ["saved", "downstream"]


def test_result_survives_downstream_failure() -> None:
    store = InMemoryQualityResultStore()
    gate = QualityGate(checker=L1_CHECKER, store=store)

    def downstream(report) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("下游失败")

    with pytest.raises(RuntimeError):
        gate.run_page(page_input(), on_saved=downstream)

    assert store.load(DOC_ID, 9) is not None


def test_store_round_trip_keeps_defects() -> None:
    store = InMemoryQualityResultStore()
    gate = QualityGate(checker=L1_CHECKER, store=store)
    page = page_input(metrics=L1Metrics(glyph_coverage=0.4, char_density=8.0))

    report = gate.run_page(page)

    loaded = store.load(DOC_ID, 9)
    assert loaded == report
    assert DefectCode.ENCODING_LOSS in {defect.code for defect in loaded.defects}


def test_checker_inputs_carry_no_artifact_or_path() -> None:
    """QC 只读且不重新解析：输入只有指标，没有文件 / 产物引用。"""
    fields = {field.name for field in dataclasses.fields(L1PageInput)}

    assert "path" not in fields
    assert "artifact" not in fields


def test_check_does_not_mutate_input_page() -> None:
    page = page_input(metrics=L1Metrics(glyph_coverage=0.3, char_density=8.0))
    before = dataclasses.asdict(page)

    L1_CHECKER.check(page)

    assert dataclasses.asdict(page) == before


def test_repair_queue_contains_only_actionable_defects() -> None:
    report = L1_CHECKER.check(
        page_input(
            metrics=L1Metrics(
                glyph_coverage=0.4,
                char_density=8.0,
                page_number_ok=False,
            )
        )
    )

    queue = defects_for_repair(report)

    assert {defect.code for defect in queue} == {DefectCode.ENCODING_LOSS}
    assert all(defect.location.page_no == 9 for defect in report.defects)
    assert DefectCode.PAGE_NUMBER_BROKEN not in {defect.code for defect in queue}
