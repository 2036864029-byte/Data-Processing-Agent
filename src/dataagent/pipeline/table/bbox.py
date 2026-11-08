"""BBox 几何工具（唯一实现）。

此前 `_area` / `_intersection_area` / `_overlap_ratio` / `page_bbox` / `union_bbox`
在 structure/build、table/router、table/anchor、table/geometry 各有一份，
口径还略有差别。这里只保留两条明确的重叠比例语义：

- `overlap_ratio`：交集 / **自身**面积（router 判“这个候选有多少被别处覆盖”）；
- `symmetric_overlap_ratio`：交集 / **较小**面积（build 判“两张表是否同一块”）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dataagent.pipeline.table.geometry import PositionedText

BBox = tuple[float, float, float, float]
EMPTY_BBOX: BBox = (0.0, 0.0, 1.0, 1.0)


def area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def intersection_area(left: BBox, right: BBox) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    if x0 >= x1 or y0 >= y1:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def overlap_ratio(bbox: BBox, other: BBox) -> float:
    """交集 / 自身面积；分母为 0 时返回 0。"""
    own = area(bbox)
    if own <= 0:
        return 0.0
    return intersection_area(bbox, other) / own


def symmetric_overlap_ratio(bbox: BBox, other: BBox) -> float:
    """交集 / 较小面积；任一面积为 0 时返回 0。"""
    smaller = min(area(bbox), area(other))
    if smaller <= 0:
        return 0.0
    return intersection_area(bbox, other) / smaller


def union_bbox(boxes: Sequence[BBox]) -> BBox:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def bbox_from_items(items: Sequence[PositionedText]) -> BBox:
    """坐标项的外接框；空序列会抛 ValueError（调用方必须保证非空）。"""
    return (
        min(item.x for item in items),
        min(item.y for item in items),
        max(item.x + item.width for item in items),
        max(item.y + item.height for item in items),
    )


def page_bbox(items: Sequence[PositionedText]) -> BBox:
    """页框；无坐标项时退回单位框（无坐标页面走 Markdown 回退路径）。"""
    if not items:
        return EMPTY_BBOX
    return bbox_from_items(items)
