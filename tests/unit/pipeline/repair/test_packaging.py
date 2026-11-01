"""失败页聚包测试（T103，B6）——FR-012。

散落失败页按 50–100 页聚包；只抽失败页、不重跑整个文件；
页码映射落盘并参与幂等（同页集合 → 同包 ID）。
"""

from dataagent.contracts.defects import RepairTier
from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.repair.fallback import FallbackPath
from dataagent.pipeline.repair.gap import PageGap
from dataagent.pipeline.repair.packaging import (
    InMemoryPageMapStore,
    build_packages,
)


class FakeExtractor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def extract(self, doc_id: str, pages: list[int]) -> bytes:
        self.calls.append((doc_id, tuple(pages)))
        return f"PDF({doc_id}:{','.join(str(page) for page in pages)})".encode()


def gap(doc_id: str, page_no: int) -> PageGap:
    return PageGap(
        doc_id=doc_id,
        page_no=page_no,
        defect_codes=(DefectCode.TABLE_STRUCTURE_BROKEN,),
        tried_operators=(),
        recommended_tier=RepairTier.T1,
        fallback_used=FallbackPath.MARK_ONLY,
    )


def gaps(count: int, docs: int = 5) -> list[PageGap]:
    result: list[PageGap] = []
    for index in range(count):
        result.append(gap(f"doc-{index % docs}", index // docs + 1))
    return result


def test_packages_are_balanced_between_50_and_100_pages() -> None:
    extractor = FakeExtractor()

    packages = build_packages(gaps(230), extractor=extractor)

    assert [len(package.pages) for package in packages] == [77, 77, 76]


def test_every_failed_page_is_packaged_exactly_once() -> None:
    source = gaps(230)

    packages = build_packages(source, extractor=FakeExtractor())

    packed = [
        (page.doc_id, page.original_page_no)
        for package in packages
        for page in package.pages
    ]
    assert sorted(packed) == sorted((item.doc_id, item.page_no) for item in source)


def test_only_failed_pages_are_extracted() -> None:
    """只抽失败页，不重跑整个文件。"""
    extractor = FakeExtractor()
    source = [gap("doc-big", page) for page in (37, 82, 155)]

    packages = build_packages(source, extractor=extractor)

    assert len(packages) == 1
    assert extractor.calls == [("doc-big", (37, 82, 155))]


def test_page_map_is_persisted_and_round_trips() -> None:
    store = InMemoryPageMapStore()
    source = gaps(120)

    packages = build_packages(source, extractor=FakeExtractor(), store=store)

    for package in packages:
        loaded = store.load(package.package_id)
        assert loaded == package.page_map
        assert [entry.package_page_no for entry in package.page_map] == list(
            range(1, len(package.pages) + 1)
        )


def test_package_id_is_idempotent_over_page_set() -> None:
    first = build_packages(gaps(60), extractor=FakeExtractor())
    second = build_packages(gaps(60), extractor=FakeExtractor())

    assert [package.package_id for package in first] == [
        package.package_id for package in second
    ]


def test_package_payload_contains_extracted_failed_pages() -> None:
    extractor = FakeExtractor()

    packages = build_packages(gaps(230), extractor=extractor)

    assert all(package.payload.startswith(b"PDF(") for package in packages)
    total_extracted = sum(len(pages) for _, pages in extractor.calls)
    assert total_extracted == 230


def test_small_batches_still_produce_a_package() -> None:
    packages = build_packages(gaps(10), extractor=FakeExtractor())

    assert len(packages) == 1
    assert len(packages[0].pages) == 10
