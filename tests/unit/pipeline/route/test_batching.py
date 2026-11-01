"""批级抽取测试（T084，B3）——FR-006。

同一文档页按批一次传入、批内不拆（默认批 32 页）；
扫描页单独成批、不与普通页混批；同一文档所有批落在同一处理单元。
文件打开次数 ≤ ⌈页数/批大小⌉ + 2（宪法性能预算）。
"""

import math

from dataagent.contracts.policies import ChainId
from dataagent.pipeline.route.batching import BatchKind, plan_batches
from dataagent.pipeline.route.routes import (
    RouteDecision,
    RouteLevel,
    RoutingOutcome,
)
from dataagent.pipeline.sniff.sniffer import Family, SniffResult, SniffState

DOC_ID = "a" * 64


def page_decision(page_no: int, chain: ChainId) -> RouteDecision:
    return RouteDecision(
        doc_id=DOC_ID,
        level=RouteLevel.PAGE,
        page_no=page_no,
        chain=chain,
        reasons=(f"route:{chain.value}",),
        profile_snapshot={"page_no": page_no},
    )


def pdf_outcome(chains: dict[int, ChainId]) -> RoutingOutcome:
    level1 = RouteDecision(
        doc_id=DOC_ID,
        level=RouteLevel.FORMAT,
        page_no=None,
        chain=None,
        reasons=("family:pdf",),
        profile_snapshot={"sniff": {}},
    )
    return RoutingOutcome(
        doc_id=DOC_ID,
        family=Family.PDF,
        level1=level1,
        page_decisions=tuple(
            page_decision(page_no, chain) for page_no, chain in chains.items()
        ),
        classification=None,
    )


def whole_doc_outcome(family: Family, chain: ChainId) -> RoutingOutcome:
    level1 = RouteDecision(
        doc_id=DOC_ID,
        level=RouteLevel.FORMAT,
        page_no=None,
        chain=chain,
        reasons=(f"family:{family.value}",),
        profile_snapshot={"sniff": SniffResult(Family.TABULAR, SniffState.CONFIDENT).as_dict()},
    )
    return RoutingOutcome(
        doc_id=DOC_ID,
        family=family,
        level1=level1,
        page_decisions=(),
        classification=None,
    )


def test_native_pages_chunked_by_default_size() -> None:
    """默认批 32 页，批内不拆，页顺序与覆盖完整。"""
    outcome = pdf_outcome(dict.fromkeys(range(1, 101), ChainId.PDF_NATIVE))

    batches = plan_batches(outcome)

    assert [len(batch.pages) for batch in batches] == [32, 32, 32, 4]
    assert [page for batch in batches for page in batch.pages] == list(range(1, 101))
    assert all(batch.kind is BatchKind.NATIVE for batch in batches)
    assert [batch.index for batch in batches] == [0, 1, 2, 3]


def test_scan_pages_form_their_own_batch() -> None:
    """扫描页单独成批，不与普通页混批（FR-006）。"""
    chains = dict.fromkeys(range(1, 101), ChainId.PDF_NATIVE)
    for page in (5, 6, 7):
        chains[page] = ChainId.PDF_OCR

    batches = plan_batches(pdf_outcome(chains))
    scanned = [batch for batch in batches if batch.kind is BatchKind.SCANNED]
    native = [batch for batch in batches if batch.kind is BatchKind.NATIVE]

    assert len(scanned) == 1
    assert scanned[0].pages == (5, 6, 7)
    assert all(not (set(batch.pages) & {5, 6, 7}) for batch in native)
    assert sorted(page for batch in batches for page in batch.pages) == list(range(1, 101))


def test_scan_pages_above_batch_size_are_split() -> None:
    """扫描页超过批大小时仍按 32 页切批。"""
    chains = dict.fromkeys(range(1, 11), ChainId.PDF_NATIVE)
    for page in range(11, 51):
        chains[page] = ChainId.PDF_OCR

    batches = plan_batches(pdf_outcome(chains))
    scanned = [batch for batch in batches if batch.kind is BatchKind.SCANNED]

    assert [len(batch.pages) for batch in scanned] == [32, 8]


def test_batch_size_override() -> None:
    outcome = pdf_outcome(dict.fromkeys(range(1, 11), ChainId.PDF_NATIVE))

    batches = plan_batches(outcome, batch_size=4)

    assert [len(batch.pages) for batch in batches] == [4, 4, 2]


def test_non_pdf_is_single_whole_document_batch() -> None:
    """非 PDF 无页结构：整文档一个批。"""
    batches = plan_batches(whole_doc_outcome(Family.TABULAR, ChainId.TABULAR))

    assert len(batches) == 1
    assert batches[0].kind is BatchKind.WHOLE
    assert batches[0].pages == (1,)


def test_all_batches_share_document_affinity() -> None:
    """同一文档所有批落在同一处理单元（FR-006）。"""
    outcome = pdf_outcome(dict.fromkeys(range(1, 101), ChainId.PDF_NATIVE))

    batches = plan_batches(outcome)

    assert {batch.doc_affinity_key for batch in batches} == {DOC_ID}


def test_batches_are_ordered_by_first_page() -> None:
    chains = dict.fromkeys(range(1, 101), ChainId.PDF_NATIVE)
    chains[5] = ChainId.PDF_OCR

    batches = plan_batches(pdf_outcome(chains))

    assert [batch.pages[0] for batch in batches] == sorted(
        batch.pages[0] for batch in batches
    )


def test_batch_count_stays_within_file_open_budget() -> None:
    """文件打开次数 ≤ ⌈页数/批大小⌉ + 2（宪法性能预算）。"""
    chains = dict.fromkeys(range(1, 101), ChainId.PDF_NATIVE)
    chains[7] = ChainId.PDF_OCR

    batches = plan_batches(pdf_outcome(chains))

    assert len(batches) <= math.ceil(100 / 32) + 2


def test_invalid_batch_size_rejected() -> None:
    outcome = pdf_outcome({1: ChainId.PDF_NATIVE})

    try:
        plan_batches(outcome, batch_size=0)
    except ValueError:
        pass
    else:  # pragma: no cover - 断言失败路径
        raise AssertionError("batch_size=0 必须拒绝")
