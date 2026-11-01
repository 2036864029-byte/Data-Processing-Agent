"""L2 分层抽样与性能档位（B5 / FR-009 / PRD 4.4.12）。

- fail 页 100% 全查（任何档位）。
- `quality`：15 页/文档分层抽样；`balanced`：5 页/文档；`fast`：fail 页 + 首末页。
- 三档只削 L2 抽样与增强轮次（fast 禁用 T3、STP 为估算值不可用于验收）；
  **L1 不在本模块决策范围内——调用方必须全量跑 L1**。
- 抽样键为内容哈希派生的确定性顺序，可重放（宪法 I）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final

from dataagent.contracts.messages import PageReport, Verdict

LOW_QUALITY_THRESHOLD: Final[float] = 0.70
MID_QUALITY_THRESHOLD: Final[float] = 0.85


@unique
class ProfileSpeed(StrEnum):
    QUALITY = "quality"
    BALANCED = "balanced"
    FAST = "fast"


@dataclass(frozen=True, slots=True)
class SpeedProfile:
    """档位只影响 L2 抽样比例与增强轮次（PRD 4.4.12 唯一合法削减顺序）。"""

    l2_pages_per_doc: int | None
    enhancement_rounds: int
    allow_t3: bool
    stp_reportable: bool


SPEED_PROFILES: Final[Mapping[ProfileSpeed, SpeedProfile]] = {
    ProfileSpeed.QUALITY: SpeedProfile(
        l2_pages_per_doc=15, enhancement_rounds=3, allow_t3=True, stp_reportable=True
    ),
    ProfileSpeed.BALANCED: SpeedProfile(
        l2_pages_per_doc=5, enhancement_rounds=2, allow_t3=True, stp_reportable=True
    ),
    ProfileSpeed.FAST: SpeedProfile(
        l2_pages_per_doc=None, enhancement_rounds=1, allow_t3=False, stp_reportable=False
    ),
}


@dataclass(frozen=True, slots=True)
class L2SamplePlan:
    doc_id: str
    speed: ProfileSpeed
    selected: tuple[int, ...]
    mandatory_fail: tuple[int, ...]
    enhancement_rounds: int
    allow_t3: bool
    stp_reportable: bool


def plan_l2_sampling(
    reports: Sequence[PageReport], *, speed: ProfileSpeed = ProfileSpeed.QUALITY
) -> L2SamplePlan:
    if not reports:
        raise ValueError("L2 抽样至少需要一页 L1 报告")
    doc_ids = {report.doc_id for report in reports}
    if len(doc_ids) != 1:
        raise ValueError("一次只能规划同一文档的页（文档亲和）")

    doc_id = reports[0].doc_id
    ordered = sorted(reports, key=lambda report: report.page_no)
    fails = tuple(
        report.page_no for report in ordered if report.verdict is Verdict.FAIL
    )
    profile = SPEED_PROFILES[speed]

    if speed is ProfileSpeed.FAST:
        selected = sorted(
            set(fails) | {ordered[0].page_no, ordered[-1].page_no}
        )
    else:
        budget = profile.l2_pages_per_doc or 0
        selected = _stratified_plus_fails(ordered, fails, budget, doc_id)

    return L2SamplePlan(
        doc_id=doc_id,
        speed=speed,
        selected=tuple(sorted(set(selected))),
        mandatory_fail=tuple(sorted(set(fails))),
        enhancement_rounds=profile.enhancement_rounds,
        allow_t3=profile.allow_t3,
        stp_reportable=profile.stp_reportable,
    )


def _stratified_plus_fails(
    ordered: Sequence[PageReport], fails: tuple[int, ...], budget: int, doc_id: str
) -> list[int]:
    """fail 页全查；其余按质量分层（低 / 中 / 高）轮转抽样至预算。"""
    fail_set = set(fails)
    bands: dict[str, list[int]] = {"low": [], "mid": [], "high": []}
    for report in ordered:
        if report.page_no in fail_set:
            continue
        bands[_band(report.extract_quality)].append(report.page_no)
    for pages in bands.values():
        pages.sort(key=lambda page_no: _sample_key(doc_id, page_no))

    selected = list(fails)
    taken = 0
    cursors = dict.fromkeys(bands, 0)
    while taken < budget:
        progressed = False
        for band in ("low", "mid", "high"):
            pages = bands[band]
            index = cursors[band]
            if index >= len(pages):
                continue
            selected.append(pages[index])
            cursors[band] = index + 1
            taken += 1
            progressed = True
            if taken >= budget:
                break
        if not progressed:
            break
    return selected


def _band(quality: float) -> str:
    if quality < LOW_QUALITY_THRESHOLD:
        return "low"
    if quality < MID_QUALITY_THRESHOLD:
        return "mid"
    return "high"


def _sample_key(doc_id: str, page_no: int) -> str:
    """确定性抽样顺序：内容哈希派生，不依赖进程内随机状态。"""
    return hashlib.sha256(f"{doc_id}:{page_no}".encode()).hexdigest()
