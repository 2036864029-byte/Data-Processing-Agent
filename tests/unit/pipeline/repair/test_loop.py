"""必写测试 #4 / #6：无进展停机与增强回路收敛（T100/T101，B6）。

- `changed == false` 立即放弃、不重试第二次；
- 同一缺陷码同一页只修一次（同码唯一）；
- 三层预算（次数 / 成本 / 耗时）耗尽升级，无无限循环；
- 采纳候选必须带来可量化提升（≥5% 且缺陷数减少），否则丢弃候选。
"""

from collections.abc import Mapping

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.defects import Defect, DefectLocation, RepairOperator
from dataagent.contracts.errors import DefectCode
from dataagent.kernel.budget import Budget, BudgetTracker
from dataagent.pipeline.repair.loop import (
    EnhancementOutcome,
    LoopLimits,
    PageProbe,
    run_enhancement,
)
from dataagent.pipeline.repair.operators import OperatorFn, OperatorOutcome

DOC_ID = "b" * 64


def artifact(markdown: str = "正文") -> PageArtifact:
    return PageArtifact(
        doc_id=DOC_ID,
        page_no=1,
        engine="engine.pdf@1.0.0",
        params_hash="params",
        markdown=markdown,
    )


def defect(code: DefectCode) -> Defect:
    return Defect(code=code, location=DefectLocation(page_no=1))


def changing_operator(calls: list[RepairOperator]) -> OperatorFn:
    def operator(
        page: PageArtifact, found: Defect, context: object
    ) -> OperatorOutcome:
        return OperatorOutcome(
            candidate=page.model_copy(update={"markdown": page.markdown + "+"}),
            changed=True,
            cost=0,
            notes="changed",
        )

    def wrapped(page: PageArtifact, found: Defect, context: object) -> OperatorOutcome:
        calls.append(RepairOperator.REBUILD_TABLE_CELLS)
        return operator(page, found, context)

    return wrapped


def unchanged_operator(calls: list[RepairOperator]) -> OperatorFn:
    def operator(
        page: PageArtifact, found: Defect, context: object
    ) -> OperatorOutcome:
        calls.append(RepairOperator.REBUILD_TABLE_CELLS)
        return OperatorOutcome(
            candidate=None, changed=False, cost=0, notes="此路不通"
        )

    return operator


def operators_for(
    operator: OperatorFn,
) -> Mapping[RepairOperator, OperatorFn]:
    return dict.fromkeys(RepairOperator, operator)


def test_changed_false_is_not_retried() -> None:
    calls: list[RepairOperator] = []
    remaining = (defect(DefectCode.TABLE_STRUCTURE_BROKEN),)

    outcome = run_enhancement(
        artifact(),
        remaining,
        baseline_quality=0.5,
        probe=lambda page: PageProbe(quality=0.5, defects=remaining),
        operators=operators_for(unchanged_operator(calls)),
    )

    assert len(calls) == 1
    assert outcome.artifact == artifact()
    assert outcome.escalated is True
    assert outcome.escalation_reason == "same_code_exhausted"


def test_same_defect_code_is_repaired_only_once() -> None:
    """同码唯一：修复后同码仍在也不换花样重试。"""
    calls: list[RepairOperator] = []
    same = (defect(DefectCode.TABLE_STRUCTURE_BROKEN),)

    outcome = run_enhancement(
        artifact(),
        same,
        baseline_quality=0.5,
        probe=lambda page: PageProbe(quality=0.9, defects=same),
        operators=operators_for(changing_operator(calls)),
    )

    assert len(calls) == 1
    assert outcome.escalated is True
    assert outcome.escalation_reason == "same_code_exhausted"


def test_rounds_limit_stops_loop() -> None:
    calls: list[RepairOperator] = []
    codes = (
        DefectCode.TABLE_STRUCTURE_BROKEN,
        DefectCode.HEADER_FOOTER_LEAK,
        DefectCode.READING_ORDER_BROKEN,
        DefectCode.COLUMN_MIXED,
    )
    pending = tuple(defect(code) for code in codes)
    seen = {"value": 0}

    def probe(page: PageArtifact) -> PageProbe:
        seen["value"] += 1
        return PageProbe(
            quality=0.5 + 0.1 * seen["value"],
            defects=pending[seen["value"] :],
        )

    outcome = run_enhancement(
        artifact(),
        pending,
        baseline_quality=0.5,
        probe=probe,
        operators=operators_for(changing_operator(calls)),
        limits=LoopLimits(max_rounds=3),
    )

    assert len(calls) == 3
    assert outcome.escalated is True
    assert outcome.escalation_reason == "rounds_exhausted"
    assert len(outcome.remaining_defects) == 1


def test_budget_exhaustion_escalates() -> None:
    calls: list[RepairOperator] = []
    codes = (
        DefectCode.TABLE_STRUCTURE_BROKEN,
        DefectCode.HEADER_FOOTER_LEAK,
        DefectCode.READING_ORDER_BROKEN,
    )
    pending = tuple(defect(code) for code in codes)
    seen = {"value": 0}

    def probe(page: PageArtifact) -> PageProbe:
        seen["value"] += 1
        return PageProbe(
            quality=0.5 + 0.1 * seen["value"],
            defects=pending[seen["value"] :],
        )

    outcome = run_enhancement(
        artifact(),
        pending,
        baseline_quality=0.5,
        probe=probe,
        operators=operators_for(changing_operator(calls)),
        budget=BudgetTracker(Budget(max_cost=100, max_attempts=2, max_duration_s=100)),
    )

    assert len(calls) == 2
    assert outcome.escalated is True
    assert outcome.escalation_reason == "budget_exhausted"


def test_quality_gain_below_five_percent_is_rejected() -> None:
    calls: list[RepairOperator] = []
    remaining = (defect(DefectCode.TABLE_STRUCTURE_BROKEN),)

    outcome = run_enhancement(
        artifact(),
        remaining,
        baseline_quality=0.5,
        probe=lambda page: PageProbe(quality=0.51, defects=()),
        operators=operators_for(changing_operator(calls)),
    )

    assert len(calls) == 1
    assert outcome.artifact == artifact()
    assert outcome.escalated is True


def test_defect_count_must_decrease_even_with_big_quality_gain() -> None:
    """无进展 = 质量提升 <5% **或** 缺陷数未减少（PRD 4.5.9）。"""
    calls: list[RepairOperator] = []
    remaining = (defect(DefectCode.TABLE_STRUCTURE_BROKEN),)

    outcome = run_enhancement(
        artifact(),
        remaining,
        baseline_quality=0.5,
        probe=lambda page: PageProbe(quality=0.9, defects=remaining),
        operators=operators_for(changing_operator(calls)),
    )

    assert len(calls) == 1
    assert outcome.artifact == artifact()


def test_candidate_is_adopted_when_gain_is_measurable() -> None:
    calls: list[RepairOperator] = []
    remaining = (
        defect(DefectCode.TABLE_STRUCTURE_BROKEN),
        defect(DefectCode.HEADER_FOOTER_LEAK),
    )
    probe_results = iter(
        [
            PageProbe(quality=0.9, defects=remaining[1:]),
            PageProbe(quality=0.95, defects=()),
        ]
    )

    outcome = run_enhancement(
        artifact(),
        remaining,
        baseline_quality=0.5,
        probe=lambda page: next(probe_results),
        operators=operators_for(changing_operator(calls)),
    )

    assert outcome.artifact.markdown == "正文++"
    assert outcome.remaining_defects == ()
    assert outcome.escalated is False
    assert len(outcome.actions) == 2


def test_time_limit_stops_loop() -> None:
    calls: list[RepairOperator] = []
    pending = (
        defect(DefectCode.TABLE_STRUCTURE_BROKEN),
        defect(DefectCode.HEADER_FOOTER_LEAK),
    )
    ticks = iter([0.0, 0.0, 5.0])

    outcome = run_enhancement(
        artifact(),
        pending,
        baseline_quality=0.5,
        probe=lambda page: PageProbe(quality=0.9, defects=pending),
        operators=operators_for(changing_operator(calls)),
        limits=LoopLimits(max_rounds=10, max_duration_s=1.0),
        clock=lambda: next(ticks),
    )

    assert outcome.escalated is True
    assert outcome.escalation_reason == "time_budget"
    assert isinstance(outcome, EnhancementOutcome)
