"""抽取侧旁车测试：按 pages_with_tables 门控、结构元素全页、非 PDF 跳过。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dataagent.contracts.artifacts import ExtractionResult, PageArtifact
from dataagent.pipeline.sniff.sniffer import Family
from dataagent.pipeline.task.extract import ExtractionService
from dataagent.platform.plugins.adapters.layout_probe import (
    ProbedItem,
    ProbedLayout,
    ProbedRole,
)


def extraction(pages_with_tables: tuple[int, ...]) -> ExtractionResult:
    return ExtractionResult(
        doc_id="doc",
        engine="engine.pdf",
        params_hash="h",
        pages=(
            PageArtifact(
                doc_id="doc",
                page_no=1,
                engine="engine.pdf",
                params_hash="h",
                markdown="正文\n",
            ),
        ),
        pages_with_tables=pages_with_tables,
    )


def context(family: Family = Family.PDF) -> SimpleNamespace:
    return SimpleNamespace(
        routing=SimpleNamespace(family=family),
        path=Path("doc.pdf"),
        record=SimpleNamespace(doc_id="doc"),
        layouts={},
        table_pages=set(),
    )


class FakeProbe:
    def __init__(self) -> None:
        self.calls: list[tuple[list[int], list[int]]] = []

    def __call__(
        self, source: str, *, item_pages: list[int], role_pages: list[int]
    ) -> ProbedLayout:
        self.calls.append((list(item_pages), list(role_pages)))
        return ProbedLayout(
            items=(
                ProbedItem(
                    page=2, text="项", x=1, y=2, width=3, height=4, mcid=9
                ),
            ),
            roles=(
                ProbedRole(page=1, mcid=0, role="H1"),
                ProbedRole(page=2, mcid=9, role="P"),
            ),
        )


def service(probe) -> ExtractionService:
    return ExtractionService(
        registry=SimpleNamespace(),
        profiles=SimpleNamespace(),
        layout_probe=probe,
    )


def test_positions_are_gated_by_table_pages_and_roles_cover_batch() -> None:
    probe = FakeProbe()
    ctx = context()

    service(probe).attach_layout(ctx, extraction((2,)), [1, 2, 3])

    assert probe.calls == [([2], [1, 2, 3])]
    assert ctx.table_pages == {2}
    assert ctx.layouts[2].items[0].mcid == 9
    assert ctx.layouts[1].items == ()
    assert ctx.layouts[1].roles == ((0, "H1"),)
    assert ctx.layouts[3].items == ()


def test_non_pdf_family_skips_probe() -> None:
    probe = FakeProbe()
    ctx = context(Family.TEXT)

    service(probe).attach_layout(ctx, extraction((1,)), [1])

    assert probe.calls == []
    assert ctx.layouts == {}


def test_probe_failure_does_not_raise() -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    ctx = context()

    service(broken).attach_layout(ctx, extraction((1,)), [1])

    assert ctx.layouts == {}
