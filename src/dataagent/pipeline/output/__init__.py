"""输出（B7）：分级交付 + 分层报告 + JSON/CSV/Markdown/Excel + 数据库写入。"""

from dataagent.pipeline.output.delivery import (
    DEFAULT_GAP_TOLERANCE,
    DeliveryDecision,
    DeliveryLevel,
    decide_delivery,
)
from dataagent.pipeline.output.formats import to_csv, to_excel, to_json, to_markdown
from dataagent.pipeline.output.reports import (
    DocSummary,
    FileRow,
    Overview,
    PageDetail,
    ProcessReport,
    ReasonAggregate,
    build_report,
)

__all__ = [
    "DEFAULT_GAP_TOLERANCE",
    "DeliveryDecision",
    "DeliveryLevel",
    "DocSummary",
    "FileRow",
    "Overview",
    "PageDetail",
    "ProcessReport",
    "ReasonAggregate",
    "build_report",
    "decide_delivery",
    "to_csv",
    "to_excel",
    "to_json",
    "to_markdown",
]
