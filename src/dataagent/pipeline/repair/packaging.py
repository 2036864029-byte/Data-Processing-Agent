"""失败页聚包 + 页码映射（B6/T107 / FR-012 / PRD 4.5.10）。

- 散落失败页按 50–100 页均衡聚包（跨文档），一包一次增强调用；
- **只抽失败页**（`pages=[...]`），不重跑整个文件；
- 页码映射（包内页号 → 原 doc_id + 原页码）落盘，并参与包幂等键：
  同一页集合无论顺序如何都生成同一 `package_id`。
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from dataagent.contracts.errors import DefectCode
from dataagent.contracts.ids import content_hash
from dataagent.pipeline.repair.fallback import FallbackPath
from dataagent.pipeline.repair.gap import PageGap

DEFAULT_MIN_PAGES = 50
DEFAULT_MAX_PAGES = 100


@dataclass(frozen=True, slots=True)
class PackagePage:
    package_page_no: int
    doc_id: str
    original_page_no: int
    defect_codes: tuple[DefectCode, ...]
    fallback_used: FallbackPath


@dataclass(frozen=True, slots=True)
class PageMapEntry:
    package_page_no: int
    doc_id: str
    original_page_no: int


@dataclass(frozen=True, slots=True)
class EnhancementPackage:
    package_id: str
    pages: tuple[PackagePage, ...]
    payload: bytes
    page_map: tuple[PageMapEntry, ...]


class FailurePageExtractor(Protocol):
    def extract(self, doc_id: str, pages: list[int]) -> bytes: ...


class PageMapStore(Protocol):
    def save(self, package_id: str, page_map: tuple[PageMapEntry, ...]) -> None: ...

    def load(self, package_id: str) -> tuple[PageMapEntry, ...] | None: ...


class InMemoryPageMapStore:
    def __init__(self) -> None:
        self._maps: dict[str, tuple[PageMapEntry, ...]] = {}

    def save(self, package_id: str, page_map: tuple[PageMapEntry, ...]) -> None:
        self._maps[package_id] = page_map

    def load(self, package_id: str) -> tuple[PageMapEntry, ...] | None:
        return self._maps.get(package_id)


def build_packages(
    gaps: Sequence[PageGap],
    *,
    extractor: FailurePageExtractor,
    store: PageMapStore | None = None,
    min_pages: int = DEFAULT_MIN_PAGES,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[EnhancementPackage, ...]:
    if min_pages > max_pages:
        raise ValueError("min_pages 不得大于 max_pages")
    if not gaps:
        return ()

    ordered = sorted(gaps, key=lambda gap: (gap.doc_id, gap.page_no))
    packages: list[EnhancementPackage] = []
    cursor = 0
    for size in _partition(len(ordered), max_pages):
        chunk = ordered[cursor : cursor + size]
        cursor += size
        packages.append(_build_one(chunk, extractor=extractor, store=store))
    return tuple(packages)


def _build_one(
    chunk: Sequence[PageGap],
    *,
    extractor: FailurePageExtractor,
    store: PageMapStore | None,
) -> EnhancementPackage:
    pages = tuple(
        PackagePage(
            package_page_no=index,
            doc_id=gap.doc_id,
            original_page_no=gap.page_no,
            defect_codes=gap.defect_codes,
            fallback_used=gap.fallback_used,
        )
        for index, gap in enumerate(chunk, start=1)
    )
    page_map = tuple(
        PageMapEntry(
            package_page_no=page.package_page_no,
            doc_id=page.doc_id,
            original_page_no=page.original_page_no,
        )
        for page in pages
    )

    by_doc: dict[str, list[int]] = {}
    for gap in chunk:
        by_doc.setdefault(gap.doc_id, []).append(gap.page_no)
    payload = b"".join(
        extractor.extract(doc_id, pages_for_doc)
        for doc_id, pages_for_doc in by_doc.items()
    )

    package_id = content_hash(
        json.dumps(
            sorted((gap.doc_id, gap.page_no) for gap in chunk),
            separators=(",", ":"),
        )
    )
    if store is not None:
        store.save(package_id, page_map)
    return EnhancementPackage(
        package_id=package_id, pages=pages, payload=payload, page_map=page_map
    )


def _partition(count: int, max_pages: int) -> list[int]:
    """均衡切包：每包 ≤ max_pages；跨包页数差 ≤ 1。"""
    if count <= max_pages:
        return [count]
    packages = math.ceil(count / max_pages)
    base, remainder = divmod(count, packages)
    return [base + (1 if index < remainder else 0) for index in range(packages)]
