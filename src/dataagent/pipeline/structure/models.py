"""PR-1 内部块模型。

这是阶段一的临时真身，只服务 md 交付；PR-2 会把字段正式迁入 contracts/structure.py
（level / block_id / table_id / rows_text）。本模块不对外承诺稳定契约。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dataagent.contracts.table import TableGrid
from dataagent.pipeline.table.geometry import PositionedText
from dataagent.pipeline.table.policy import TableSource

BBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class PageLayout:
    """页级旁车：坐标项 + 结构元素角色（mcid → role）。"""

    page_no: int
    items: tuple[PositionedText, ...] = ()
    roles: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class TableSlot:
    grid: TableGrid
    bbox: BBox
    source: TableSource
    trusted_bbox: bool = True
    caption: str | None = None
    span: tuple[int, int] | None = None
    table_id: str | None = None


@dataclass(frozen=True, slots=True)
class Block:
    kind: Literal["heading", "text", "table"]
    page_no: int
    bbox: BBox
    text: str = ""
    level: int | None = None
    table: TableSlot | None = None


@dataclass(frozen=True, slots=True)
class PageBlocks:
    page_no: int
    blocks: tuple[Block, ...] = ()
    gap: bool = False


@dataclass(frozen=True, slots=True)
class DocumentBlocks:
    doc_id: str
    pages: tuple[PageBlocks, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PageBuildResult:
    page: PageBlocks
    warnings: tuple[str, ...] = ()
