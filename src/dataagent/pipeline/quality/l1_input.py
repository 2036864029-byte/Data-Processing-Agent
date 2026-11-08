"""L1 输入装配：产物 + 页型 + 表格先验 → `L1PageInput`。

生产（`PageQualityService`）与回归测试共用这一份，避免测试里重写一遍
“伪表过滤 + has_table 推导”的判定而与生产漂移。
"""

from __future__ import annotations

from dataclasses import replace

from dataagent.contracts.policies import PdfType
from dataagent.pipeline.quality.l1_check import L1Metrics, L1PageInput
from dataagent.pipeline.quality.table_metrics import table_page_metrics
from dataagent.pipeline.table.policy import DEFAULT_TABLE_POLICY, TablePolicy


def l1_metrics(
    markdown: str,
    *,
    text_area: float,
    declared_table: bool,
    policy: TablePolicy = DEFAULT_TABLE_POLICY,
) -> L1Metrics:
    """文本指标 + （有表时）真实表指标；伪表格在这里被剔除。"""
    metrics = L1Metrics.from_text(markdown, text_area=text_area)
    if not declared_table:
        return metrics
    table = table_page_metrics(markdown, skip_prose=True, policy=policy)
    if table is None:
        return metrics
    return replace(
        metrics,
        table_cell_fill=table.cell_fill,
        row_col_consistency=table.column_variance,
        table_header_ok=table.header_ok,
        row_occupancy=table.row_occupancy,
        col_occupancy=table.col_occupancy,
    )


def build_l1_input(
    *,
    doc_id: str,
    page_no: int,
    markdown: str,
    pdf_type: PdfType,
    declared_table: bool,
    has_columns: bool,
    text_area: float,
    policy: TablePolicy = DEFAULT_TABLE_POLICY,
) -> L1PageInput:
    metrics = l1_metrics(
        markdown,
        text_area=text_area,
        declared_table=declared_table,
        policy=policy,
    )
    return L1PageInput(
        doc_id=doc_id,
        page_no=page_no,
        metrics=metrics,
        pdf_type=pdf_type,
        # 伪表格已被过滤：只有真实表才算表格页，避免假缺口（R-0002）。
        has_table=metrics.table_cell_fill is not None,
        has_columns=has_columns,
    )
