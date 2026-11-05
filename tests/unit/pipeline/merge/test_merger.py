"""合并测试（T108，B7）——FR-017。

按页码重排、缺口占位、文档级校验；跨页表合并已移至结构层
（`pipeline/structure/crosspage.py`），标题平滑移至结构视图。
"""

import pytest

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.merge.merger import (
    MergedPage,
    ValidationFinding,
    ValidationResult,
    merge_document,
)
from dataagent.pipeline.repair.fallback import FallbackPath
from dataagent.pipeline.repair.gap import PageGap

DOC_ID = "a" * 64


def artifact(page_no: int, markdown: str) -> PageArtifact:
    return PageArtifact(
        doc_id=DOC_ID,
        page_no=page_no,
        engine="engine.pdf@1.0.0",
        params_hash="params",
        markdown=markdown,
    )


def gap(page_no: int, code: DefectCode = DefectCode.TABLE_STRUCTURE_BROKEN) -> PageGap:
    return PageGap(
        doc_id=DOC_ID,
        page_no=page_no,
        defect_codes=(code,),
        fallback_used=FallbackPath.MARK_ONLY,
    )


TABLE_A = "| item | amount |\n| --- | --- |\n| alpha | 1 |\n"
TABLE_B = "| item | amount |\n| --- | --- |\n| beta | 2 |\n"


def test_pages_are_sorted_by_page_no() -> None:
    document = merge_document(
        DOC_ID,
        [artifact(3, "third\n"), artifact(1, "first\n"), artifact(2, "second\n")],
        [],
    )

    assert [page.page_no for page in document.pages] == [1, 2, 3]
    assert document.markdown.index("first") < document.markdown.index("second")
    assert document.markdown.index("second") < document.markdown.index("third")


def test_gap_page_keeps_placeholder_without_shifting_page_numbers() -> None:
    document = merge_document(
        DOC_ID,
        [artifact(1, "page one\n"), artifact(2, "page two\n"), artifact(4, "page four\n")],
        [gap(3)],
    )

    assert [page.page_no for page in document.pages] == [1, 2, 3, 4]
    placeholder = document.pages[2]
    assert placeholder.gap is True
    assert "dataagent:gap" in placeholder.markdown
    assert "第 3 页" in placeholder.markdown or "3" in placeholder.markdown
    assert document.gap_pages == (3,)
    assert "page four" in document.pages[3].markdown


def test_heading_levels_are_preserved_for_structure_smoothing() -> None:
    document = merge_document(
        DOC_ID,
        [artifact(1, "### 第一章\n正文\n##### 子节\n# 附录\n")],
        [],
    )

    markdown = document.pages[0].markdown
    assert "### 第一章" in markdown
    assert "##### 子节" in markdown
    assert "# 附录" in markdown


def test_document_validation_is_invoked() -> None:
    seen: list[tuple[int, ...]] = []

    class Validator:
        def validate(
            self, doc_id: str, pages: list[MergedPage]
        ) -> ValidationResult:
            seen.append(tuple(page.page_no for page in pages))
            return ValidationResult(
                ok=False,
                findings=(
                    ValidationFinding(
                        code="balance_mismatch",
                        message="借贷不平",
                        page_no=1,
                    ),
                ),
            )

    document = merge_document(
        DOC_ID, [artifact(1, "a\n"), artifact(2, "b\n")], [], validator=Validator()
    )

    assert seen == [(1, 2)]
    assert document.validation.ok is False
    assert document.validation.findings[0].code == "balance_mismatch"


def test_merge_is_deterministic() -> None:
    first = merge_document(DOC_ID, [artifact(1, TABLE_A), artifact(2, TABLE_B)], [])
    second = merge_document(DOC_ID, [artifact(1, TABLE_A), artifact(2, TABLE_B)], [])

    assert first == second


def test_duplicate_page_numbers_rejected() -> None:
    with pytest.raises(ValueError):
        merge_document(
            DOC_ID, [artifact(1, "a\n"), artifact(1, "b\n")], []
        )
