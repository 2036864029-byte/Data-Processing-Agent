"""坐标项与结构元素探测适配器（PR-1 抽取侧增量）。

页码约定（实测 pdf-inspector 1.25.2）：
- `extract_text_with_positions` 与 `extract_structure_elements` 的 `pages=` 与返回
  `page` 均为 1-indexed，直接使用系统内部页码，不经过 contracts/pages.py 的
  0-indexed 转换；
- 与 `extract_pages_markdown`（0-indexed）不同，调用方不要混用。

本模块把 pdf-inspector 对象转换为 platform 层 DTO，pipeline 不直接依赖第三方类型。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdf_inspector


@dataclass(frozen=True, slots=True)
class ProbedItem:
    page: int
    text: str
    x: float
    y: float
    width: float
    height: float
    mcid: int | None = None


@dataclass(frozen=True, slots=True)
class ProbedRole:
    page: int
    mcid: int
    role: str


@dataclass(frozen=True, slots=True)
class ProbedLayout:
    items: tuple[ProbedItem, ...] = ()
    roles: tuple[ProbedRole, ...] = ()


def probe_layout(
    source: str | Path | bytes,
    *,
    item_pages: Sequence[int],
    role_pages: Sequence[int],
) -> ProbedLayout:
    """按页探测坐标项与结构元素；空页集合跳过对应调用（零成本门控）。"""
    data = source if isinstance(source, (bytes, bytearray)) else None
    location: Any = bytes(data) if data is not None else str(source)

    items: tuple[ProbedItem, ...] = ()
    if item_pages:
        call = (
            pdf_inspector.extract_text_with_positions_bytes
            if data is not None
            else pdf_inspector.extract_text_with_positions
        )
        items = tuple(
            ProbedItem(
                page=int(value.page),
                text=str(value.text or ""),
                x=float(value.x),
                y=float(value.y),
                width=float(value.width),
                height=float(value.height),
                mcid=None if value.mcid is None else int(value.mcid),
            )
            for value in call(location, pages=list(item_pages))
        )

    roles: tuple[ProbedRole, ...] = ()
    if role_pages:
        call_roles = (
            pdf_inspector.extract_structure_elements_bytes
            if data is not None
            else pdf_inspector.extract_structure_elements
        )
        roles = tuple(
            ProbedRole(page=int(value.page), mcid=int(value.mcid), role=str(value.role))
            for value in call_roles(location, pages=list(role_pages))
        )

    return ProbedLayout(items=items, roles=roles)
