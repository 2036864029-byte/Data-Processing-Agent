"""单文档执行上下文（各阶段共享的显式状态）。

字段按阶段用途分组注释；后续阶段服务只应读写自己负责的部分。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.messages import PageReport
from dataagent.contracts.structure import DocumentStructure
from dataagent.pipeline.ingest.ingestor import DocumentRecord
from dataagent.pipeline.merge.merger import MergedDocument
from dataagent.pipeline.output.delivery import DeliveryDecision
from dataagent.pipeline.repair.gap import PageGap
from dataagent.pipeline.route.routes import RoutingOutcome
from dataagent.pipeline.sniff.sniffer import SniffResult
from dataagent.pipeline.structure.models import DocumentBlocks, PageLayout


@dataclass
class DocumentContext:
    # 接入 / 路由阶段
    record: DocumentRecord
    path: Path
    sniff: SniffResult
    routing: RoutingOutcome
    universe: tuple[int, ...]
    # 抽 / 质检 / 修复阶段
    artifacts: dict[int, PageArtifact] = field(default_factory=dict)
    reports: dict[int, PageReport] = field(default_factory=dict)
    gaps: dict[int, PageGap] = field(default_factory=dict)
    processed: set[int] = field(default_factory=set)
    # PR-1 抽取侧旁车（坐标项/结构角色）；建树后释放
    layouts: dict[int, PageLayout] = field(default_factory=dict)
    table_pages: set[int] = field(default_factory=set)
    # 交付阶段
    merged: MergedDocument | None = None
    blocks: DocumentBlocks | None = None
    structure: DocumentStructure | None = None
    meta: dict[str, Any] | None = None
    delivery: DeliveryDecision | None = None
    outputs: dict[str, str] = field(default_factory=dict)

    def release_payload(self) -> None:
        """交付写盘后释放重负载字段，保留缺口 / 报告 / 产物路径。"""
        self.merged = None
        self.blocks = None
        self.structure = None
        self.meta = None
        self.layouts.clear()
