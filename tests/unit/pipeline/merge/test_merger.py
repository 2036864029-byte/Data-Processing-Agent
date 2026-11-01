"""合并测试（T108，B7）——FR-017。

按页码重排、跨页表合并（表头列名相似度 + 列数一致性 + 触页边界）、
标题层级重建、文档级校验；缺口页保留占位、不导致页码错乱（US2 #1）。
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
TABLE_C = "| item | amount |\n| --- | --- |\n| gamma | 3 |\n"


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


def test_cross_page_table_merged_on_similar_headers() -> None:
    document = merge_document(
        DOC_ID,
        [artifact(1, "正文\n\n" + TABLE_A), artifact(2, TABLE_B + "尾注\n")],
        [],
    )

    first, second = document.pages
    assert "| beta | 2 |" in first.markdown
    assert "| alpha | 1 |" in first.markdown
    assert "| item | amount |" not in second.markdown
    assert "| beta | 2 |" not in second.markdown
    assert "尾注" in second.markdown


def test_cross_page_table_requires_column_count_match() -> None:
    wider = "| item | amount | unit |\n| --- | --- | --- |\n| beta | 2 | kg |\n"

    document = merge_document(
        DOC_ID, [artifact(1, TABLE_A), artifact(2, wider)], []
    )

    assert "| beta | 2 | kg |" in document.pages[1].markdown
    assert "| beta | 2 | kg |" not in document.pages[0].markdown


def test_cross_page_table_requires_similar_headers() -> None:
    different = "| name | value |\n| --- | --- |\n| beta | 2 |\n"

    document = merge_document(
        DOC_ID, [artifact(1, TABLE_A), artifact(2, different)], []
    )

    assert "| beta | 2 |" in document.pages[1].markdown


def test_cross_page_merge_requires_boundary_tables() -> None:
    document = merge_document(
        DOC_ID, [artifact(1, TABLE_A + "正文A\n"), artifact(2, TABLE_B)], []
    )

    assert "| beta | 2 |" in document.pages[1].markdown
    assert "| beta | 2 |" not in document.pages[0].markdown


def test_multi_page_table_chain_merges_into_first_page() -> None:
    document = merge_document(
        DOC_ID,
        [artifact(1, TABLE_A), artifact(2, TABLE_B), artifact(3, TABLE_C)],
        [],
    )

    first = document.pages[0].markdown
    assert "| alpha | 1 |" in first
    assert "| beta | 2 |" in first
    assert "| gamma | 3 |" in first
    assert "| item | amount |" not in document.pages[1].markdown
    assert "| item | amount |" not in document.pages[2].markdown


def test_heading_levels_are_rebuilt_without_skips() -> None:
    document = merge_document(
        DOC_ID,
        [artifact(1, "### 第一章\n正文\n##### 子节\n# 附录\n")],
        [],
    )

    markdown = document.pages[0].markdown
    assert markdown.startswith("# 第一章")
    assert "## 子节" in markdown
    assert "# 附录" in markdown
    assert "###" not in markdown


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
