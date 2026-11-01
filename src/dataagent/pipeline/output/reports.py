"""分层报告（B7/T113 / FR-019 / PRD 4.7.5）。

① 任务总览（10 个数字）② 按原因聚合（≤10 行）③ 文件清单（仅含缺口文件）
④ 按需明细（不预生成，调用 `detail(doc_id)` 时才返回）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.output.delivery import DeliveryLevel
from dataagent.pipeline.repair.fallback import FallbackPath

MAX_REASON_ROWS: Final[int] = 10


@dataclass(frozen=True, slots=True)
class PageDetail:
    doc_id: str
    page_no: int
    extract_quality: float
    defect_codes: tuple[DefectCode, ...] = ()
    fallback_used: FallbackPath = FallbackPath.MARK_ONLY


@dataclass(frozen=True, slots=True)
class DocSummary:
    doc_id: str
    pages: int
    gap_pages: int
    level: DeliveryLevel = DeliveryLevel.COMPLETE
    cost: float = 0.0
    failure_counts: tuple[tuple[DefectCode, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Overview:
    total_docs: int
    docs_with_gaps: int
    total_pages: int
    gap_pages: int
    gap_ratio: float
    cost_total: float
    duration_s: float
    top_failure: str | None


@dataclass(frozen=True, slots=True)
class ReasonAggregate:
    reason: DefectCode
    doc_count: int
    page_count: int


@dataclass(frozen=True, slots=True)
class FileRow:
    doc_id: str
    pages: int
    gap_pages: int
    level: DeliveryLevel


@dataclass(frozen=True, slots=True)
class ProcessReport:
    overview: Overview
    by_reason: tuple[ReasonAggregate, ...]
    files: tuple[FileRow, ...]
    _details: Mapping[str, tuple[PageDetail, ...]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def detail(self, doc_id: str) -> tuple[PageDetail, ...]:
        """按需明细：只对缺口文件返回，未提供的文件返回空。"""
        return self._details.get(doc_id, ())

    def as_dict(self) -> dict[str, Any]:
        return {
            "overview": {
                "total_docs": self.overview.total_docs,
                "docs_with_gaps": self.overview.docs_with_gaps,
                "total_pages": self.overview.total_pages,
                "gap_pages": self.overview.gap_pages,
                "gap_ratio": round(self.overview.gap_ratio, 6),
                "cost_total": self.overview.cost_total,
                "duration_s": self.overview.duration_s,
                "top_failure": self.overview.top_failure,
            },
            "by_reason": [
                {
                    "reason": row.reason.value,
                    "doc_count": row.doc_count,
                    "page_count": row.page_count,
                }
                for row in self.by_reason
            ],
            "files": [
                {
                    "doc_id": row.doc_id,
                    "pages": row.pages,
                    "gap_pages": row.gap_pages,
                    "level": row.level.value,
                }
                for row in self.files
            ],
        }

    def to_markdown(self) -> str:
        overview = self.overview
        lines = [
            "# 处理报告",
            "",
            "## 任务总览",
            f"- 文档总数：{overview.total_docs}",
            f"- 有缺口文档：{overview.docs_with_gaps}",
            f"- 累计页数：{overview.total_pages}",
            f"- 缺口页数：{overview.gap_pages}（{overview.gap_ratio:.1%}）",
            f"- 总成本：{overview.cost_total:g}",
            f"- 耗时：{overview.duration_s:g}s",
            f"- Top 失败原因：{overview.top_failure or '无'}",
            "",
            "## 按原因聚合",
            "| 原因 | 文件数 | 页数 |",
            "| --- | --- | --- |",
        ]
        lines.extend(
            f"| {row.reason.value} | {row.doc_count} | {row.page_count} |"
            for row in self.by_reason
        )
        lines.extend(
            [
                "",
                "## 文件清单（仅含缺口文件）",
                "| 文档 | 页数 | 缺口页 | 交付等级 |",
                "| --- | --- | --- | --- |",
            ]
        )
        lines.extend(
            f"| {row.doc_id} | {row.pages} | {row.gap_pages} | {row.level.value} |"
            for row in self.files
        )
        return "\n".join(lines) + "\n"


def build_report(
    summaries: Sequence[DocSummary],
    *,
    duration_s: float = 0.0,
    cost_total: float | None = None,
    page_details: Mapping[str, Sequence[PageDetail]] | None = None,
) -> ProcessReport:
    total_docs = len(summaries)
    total_pages = sum(summary.pages for summary in summaries)
    gap_pages = sum(summary.gap_pages for summary in summaries)
    with_gaps = [summary for summary in summaries if summary.gap_pages > 0]

    reasons = _aggregate_reasons(with_gaps)
    files = tuple(
        FileRow(
            doc_id=summary.doc_id,
            pages=summary.pages,
            gap_pages=summary.gap_pages,
            level=summary.level,
        )
        for summary in sorted(with_gaps, key=lambda item: (-item.gap_pages, item.doc_id))
    )
    top_failure = (
        f"{reasons[0].reason.value}"
        f"（{reasons[0].doc_count} 个文件 / {reasons[0].page_count} 页）"
        if reasons
        else None
    )
    overview = Overview(
        total_docs=total_docs,
        docs_with_gaps=len(with_gaps),
        total_pages=total_pages,
        gap_pages=gap_pages,
        gap_ratio=gap_pages / total_pages if total_pages else 0.0,
        cost_total=(
            cost_total
            if cost_total is not None
            else sum(summary.cost for summary in summaries)
        ),
        duration_s=duration_s,
        top_failure=top_failure,
    )

    gap_doc_ids = {summary.doc_id for summary in with_gaps}
    details = {
        doc_id: tuple(entries)
        for doc_id, entries in (page_details or {}).items()
        if doc_id in gap_doc_ids
    }
    return ProcessReport(
        overview=overview,
        by_reason=reasons,
        files=files,
        _details=details,
    )


def _aggregate_reasons(
    summaries: Sequence[DocSummary],
) -> tuple[ReasonAggregate, ...]:
    docs: dict[DefectCode, int] = {}
    pages: dict[DefectCode, int] = {}
    for summary in summaries:
        for code, count in summary.failure_counts:
            docs[code] = docs.get(code, 0) + 1
            pages[code] = pages.get(code, 0) + count
    ordered = sorted(
        pages, key=lambda code: (-pages[code], code.value)
    )
    return tuple(
        ReasonAggregate(reason=code, doc_count=docs[code], page_count=pages[code])
        for code in ordered[:MAX_REASON_ROWS]
    )
