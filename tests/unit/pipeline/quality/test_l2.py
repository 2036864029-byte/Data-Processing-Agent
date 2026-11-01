"""L2 分层抽样与性能档位测试（T094，B5）——FR-009 / PRD 4.4.12。

- 低质量（fail）页 100% 全查；
- `profile_speed` 三档只削 L2 抽样比例与增强轮次，**L1 始终全量**；
- 抽样确定性可重放。
"""

import pytest

from dataagent.contracts.messages import PageReport
from dataagent.pipeline.quality.l1_check import L1_CHECKER, L1Metrics, L1PageInput
from dataagent.pipeline.quality.l2_sample import (
    SPEED_PROFILES,
    ProfileSpeed,
    plan_l2_sampling,
)

DOC_ID = "b" * 64


def report_for(page_no: int, *, low_quality: bool = False) -> PageReport:
    metrics = L1Metrics(
        char_density=0.2 if low_quality else 8.0,
        glyph_coverage=0.99,
        cid_unmapped_ratio=0.001,
        empty_region_ratio=0.05,
        reading_order_score=0.97,
        header_footer_leak=0.01,
        page_number_ok=True,
    )
    return L1_CHECKER.check(
        L1PageInput(doc_id=DOC_ID, page_no=page_no, metrics=metrics)
    )


def reports(count: int, *, failing: set[int] | None = None) -> list[PageReport]:
    failing = failing or set()
    return [report_for(page, low_quality=page in failing) for page in range(1, count + 1)]


def test_quality_tier_samples_fifteen_pages() -> None:
    plan = plan_l2_sampling(reports(100), speed=ProfileSpeed.QUALITY)

    assert len(plan.selected) == 15
    assert plan.enhancement_rounds == 3
    assert plan.allow_t3 is True
    assert plan.stp_reportable is True


def test_balanced_tier_samples_five_pages() -> None:
    plan = plan_l2_sampling(reports(100), speed=ProfileSpeed.BALANCED)

    assert len(plan.selected) == 5
    assert plan.enhancement_rounds == 2
    assert plan.allow_t3 is True


def test_fast_tier_checks_fail_pages_plus_first_and_last() -> None:
    plan = plan_l2_sampling(
        reports(100, failing={50}), speed=ProfileSpeed.FAST
    )

    assert plan.selected == (1, 50, 100)
    assert plan.enhancement_rounds == 1
    assert plan.allow_t3 is False
    assert plan.stp_reportable is False


def test_fail_pages_are_always_checked_in_every_tier() -> None:
    failing = set(range(30, 40))
    for speed in ProfileSpeed:
        plan = plan_l2_sampling(reports(100, failing=failing), speed=speed)
        assert failing.issubset(set(plan.selected))


def test_sampling_covers_all_quality_bands() -> None:
    plan = plan_l2_sampling(reports(100), speed=ProfileSpeed.QUALITY)

    assert len(set(plan.selected)) == len(plan.selected)
    assert plan.selected == tuple(sorted(plan.selected))


def test_sampling_is_deterministic() -> None:
    first = plan_l2_sampling(reports(100), speed=ProfileSpeed.QUALITY)
    second = plan_l2_sampling(reports(100), speed=ProfileSpeed.QUALITY)

    assert first.selected == second.selected


def test_l1_stays_full_in_every_tier() -> None:
    """L1 全量不受档位影响：所有页都有 L1 报告，fail 集合三档一致。"""
    l1_reports = reports(100, failing={7, 42})

    fail_sets = []
    for speed in ProfileSpeed:
        plan = plan_l2_sampling(l1_reports, speed=speed)
        fail_sets.append(frozenset(plan.mandatory_fail))

    assert len(l1_reports) == 100
    assert all(fail_set == {7, 42} for fail_set in fail_sets)


def test_speed_profiles_only_reduce_l2_and_enhancement() -> None:
    quality = SPEED_PROFILES[ProfileSpeed.QUALITY]
    fast = SPEED_PROFILES[ProfileSpeed.FAST]

    assert quality.l2_pages_per_doc == 15
    assert fast.l2_pages_per_doc is None  # fail 页 + 首末页
    assert quality.enhancement_rounds > fast.enhancement_rounds
    assert quality.allow_t3 is True and fast.allow_t3 is False


def test_mixed_documents_rejected() -> None:
    mixed = reports(2) + [
        L1_CHECKER.check(
            L1PageInput(
                doc_id="c" * 64,
                page_no=1,
                metrics=L1Metrics(glyph_coverage=0.99, char_density=8.0),
            )
        )
    ]

    with pytest.raises(ValueError):
        plan_l2_sampling(mixed, speed=ProfileSpeed.QUALITY)


def test_empty_reports_rejected() -> None:
    with pytest.raises(ValueError):
        plan_l2_sampling([], speed=ProfileSpeed.QUALITY)
