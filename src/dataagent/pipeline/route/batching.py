"""批次规划（FR-006）：默认 32 页一批、批内不拆、扫描页单独成批。

- 同一文档所有批携带同一 `doc_affinity_key`（内容哈希），落在同一处理单元。
- 非 PDF（CSV / TXT）无页结构：整文档一个批。
- 批数 ≤ ⌈普通页/批大小⌉ + ⌈扫描页/批大小⌉，满足文件打开次数预算（≤ ⌈页数/批大小⌉ + 2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final

from dataagent.contracts.policies import ChainId
from dataagent.pipeline.route.routes import RoutingOutcome

DEFAULT_BATCH_PAGES: Final[int] = 32

_SCANNED_CHAINS: Final[frozenset[ChainId]] = frozenset(
    {ChainId.PDF_OCR, ChainId.PDF_VLM}
)


@unique
class BatchKind(StrEnum):
    WHOLE = "whole"
    NATIVE = "native"
    SCANNED = "scanned"


@dataclass(frozen=True, slots=True)
class PageBatch:
    """一个批级处理单元；`pages` 为内部 1-indexed 页码（保持文档顺序）。"""

    index: int
    kind: BatchKind
    pages: tuple[int, ...]
    doc_affinity_key: str


def plan_batches(
    outcome: RoutingOutcome, *, batch_size: int = DEFAULT_BATCH_PAGES
) -> list[PageBatch]:
    if batch_size < 1:
        raise ValueError(f"batch_size 必须 ≥ 1，收到 {batch_size}")

    affinity = outcome.doc_id
    if not outcome.page_decisions:
        return [PageBatch(index=0, kind=BatchKind.WHOLE, pages=(1,), doc_affinity_key=affinity)]

    groups: dict[BatchKind, list[int]] = {
        BatchKind.NATIVE: [],
        BatchKind.SCANNED: [],
    }
    for decision in outcome.page_decisions:
        if decision.page_no is None:
            continue
        kind = (
            BatchKind.SCANNED
            if decision.chain in _SCANNED_CHAINS
            else BatchKind.NATIVE
        )
        groups[kind].append(decision.page_no)

    planned: list[tuple[BatchKind, tuple[int, ...]]] = []
    for kind in (BatchKind.NATIVE, BatchKind.SCANNED):
        pages = groups[kind]
        for start in range(0, len(pages), batch_size):
            planned.append((kind, tuple(pages[start : start + batch_size])))

    planned.sort(key=lambda item: item[1][0])
    return [
        PageBatch(
            index=index,
            kind=kind,
            pages=pages,
            doc_affinity_key=affinity,
        )
        for index, (kind, pages) in enumerate(planned)
    ]
