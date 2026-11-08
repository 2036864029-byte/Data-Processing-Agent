"""质量修复相关的结构化观测事件（事件名与字段的唯一来源）。

三个事件回答三个“为什么”：
- `page_gap`：这一页为什么成了缺口——缺陷码 + 主指标/阈值 + 页型/文本层；
- `heading_decision`：这一行标题的层级从哪来——编号语义、声明层级、锁层或跳级下调；
- `normalization_applied`：这段文本被改了什么——空格、硬换行回流、编号、单元格。

只做事件封装，不改变任何判定逻辑；调用点负责决定何时发。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from dataagent.contracts.defects import Defect
from dataagent.platform.observe.log import log_event

MAX_EVENT_TEXT: Final[int] = 80

# 事件里报告的“主指标”：按诊断价值排序，取第一个在缺陷证据中出现的。
_PRIMARY_METRICS: Final[tuple[str, ...]] = (
    "char_density",
    "row_col_variance",
    "table_cell_fill",
    "empty_region_ratio",
    "glyph_coverage",
    "cid_unmapped_ratio",
)


def primary_metric(
    defects: Sequence[Defect],
) -> tuple[str | None, float | None, float | None]:
    """取缺陷证据里的主指标、实际值与阈值；无指标时返回 (None, None, None)。"""
    for defect in defects:
        for name in _PRIMARY_METRICS:
            if name not in defect.evidence:
                continue
            return (
                name,
                _number(defect.evidence[name]),
                _number(defect.evidence.get("threshold")),
            )
    return None, None, None


def log_page_gap(
    *,
    doc_id: str,
    page_no: int,
    codes: Sequence[str],
    defects: Sequence[Defect] = (),
    page_type: str | None = None,
    text_layer: str | None = None,
    source: str,
    fallback: str | None = None,
    tier: str | None = None,
) -> None:
    metric, value, threshold = primary_metric(defects)
    log_event(
        "page_gap",
        doc_id=doc_id,
        page_no=page_no,
        defect_codes=list(codes),
        metric=metric,
        value=value,
        threshold=threshold,
        page_type=page_type,
        text_layer=text_layer,
        source=source,
        fallback=fallback,
        tier=tier,
    )


def log_heading_decision(
    *,
    page_no: int,
    text: str,
    source: str,
    level: int,
    reason: str,
) -> None:
    log_event(
        "heading_decision",
        page_no=page_no,
        text=text.strip()[:MAX_EVENT_TEXT],
        source=source,
        level=level,
        reason=reason,
    )


def log_normalization_applied(*, page_no: int, kind: str, changed: bool) -> None:
    log_event(
        "normalization_applied",
        page_no=page_no,
        kind=kind,
        changed=changed,
    )


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None
