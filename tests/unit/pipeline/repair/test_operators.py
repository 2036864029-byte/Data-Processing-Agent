"""必写测试 #3：算子不可变且幂等（T099，B6）——G5。

算子返回新对象、不修改原产物；同输入两次调用候选一致（幂等）。
T1 后处理（表格 cell 重建 / 页眉页脚剔除 / 跨页表合并）为 P0 自建重点。
"""

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.defects import Defect, DefectLocation, RepairOperator
from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.repair.operators import (
    OPERATORS,
    OperatorContext,
    run_operator,
)

DOC_ID = "a" * 64


def artifact(markdown: str, page_no: int = 1) -> PageArtifact:
    return PageArtifact(
        doc_id=DOC_ID,
        page_no=page_no,
        engine="engine.pdf@1.0.0",
        params_hash="params",
        markdown=markdown,
    )


def defect(code: DefectCode, page_no: int = 1) -> Defect:
    return Defect(code=code, location=DefectLocation(page_no=page_no))


def context(page_no: int = 1, **overrides: object) -> OperatorContext:
    values: dict[str, object] = {"doc_id": DOC_ID, "page_no": page_no}
    values.update(overrides)
    return OperatorContext(**values)  # type: ignore[arg-type]


def test_all_default_operators_are_registered() -> None:
    assert set(OPERATORS) == set(RepairOperator)


def test_rebuild_table_cells_pads_ragged_rows() -> None:
    original = artifact(
        "前言\n\n| a | b | c |\n| --- | --- | --- |\n| 1 | 2 |\n| 3 | 4 | 5 |\n"
    )

    first = run_operator(
        RepairOperator.REBUILD_TABLE_CELLS,
        original,
        defect(DefectCode.TABLE_STRUCTURE_BROKEN),
        context(),
    )
    second = run_operator(
        RepairOperator.REBUILD_TABLE_CELLS,
        original,
        defect(DefectCode.TABLE_STRUCTURE_BROKEN),
        context(),
    )

    assert first.changed is True
    assert first.candidate is not None
    assert first.candidate.markdown != original.markdown
    assert "| 1 | 2 |  |" in first.candidate.markdown
    assert first.candidate.markdown == second.candidate.markdown
    assert original.markdown.endswith("| 1 | 2 |\n| 3 | 4 | 5 |\n")
    assert first.cost == 0


def test_rebuild_table_cells_is_idempotent_on_its_own_output() -> None:
    original = artifact("| a | b | c |\n| --- | --- | --- |\n| 1 | 2 |\n")

    first = run_operator(
        RepairOperator.REBUILD_TABLE_CELLS,
        original,
        defect(DefectCode.TABLE_STRUCTURE_BROKEN),
        context(),
    )
    second = run_operator(
        RepairOperator.REBUILD_TABLE_CELLS,
        first.candidate,
        defect(DefectCode.TABLE_STRUCTURE_BROKEN),
        context(),
    )

    assert first.changed is True
    assert second.changed is False
    assert second.candidate is None


def test_strip_header_footer_removes_page_number_lines() -> None:
    original = artifact("第 3 页\n\n正文第一段\n\n- 3 -\n")

    first = run_operator(
        RepairOperator.STRIP_HEADER_FOOTER,
        original,
        defect(DefectCode.HEADER_FOOTER_LEAK),
        context(),
    )
    second = run_operator(
        RepairOperator.STRIP_HEADER_FOOTER,
        first.candidate,
        defect(DefectCode.HEADER_FOOTER_LEAK),
        context(),
    )

    assert first.changed is True
    assert first.candidate is not None
    assert first.candidate.markdown == "正文第一段\n"
    assert original.markdown.startswith("第 3 页")
    assert second.changed is False


def test_strip_header_footer_leaves_clean_text_unchanged() -> None:
    original = artifact("正文\n\n第二段\n")

    outcome = run_operator(
        RepairOperator.STRIP_HEADER_FOOTER,
        original,
        defect(DefectCode.HEADER_FOOTER_LEAK),
        context(),
    )

    assert outcome.changed is False
    assert outcome.candidate is None


def test_merge_cross_page_table_requires_next_page() -> None:
    current = artifact("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    nxt = artifact("| a | b |\n| --- | --- |\n| 3 | 4 |\n", page_no=2)

    without_next = run_operator(
        RepairOperator.MERGE_CROSS_PAGE_TABLE,
        current,
        defect(DefectCode.TABLE_CROSS_PAGE_BREAK),
        context(),
    )
    merged = run_operator(
        RepairOperator.MERGE_CROSS_PAGE_TABLE,
        current,
        defect(DefectCode.TABLE_CROSS_PAGE_BREAK),
        context(next_artifact=nxt),
    )

    assert without_next.changed is False
    assert merged.changed is True
    assert merged.candidate is not None
    assert "| 3 | 4 |" in merged.candidate.markdown
    assert current.markdown.count("| 3 | 4 |") == 0
    assert nxt.markdown.count("| 3 | 4 |") == 1


def test_merge_cross_page_table_rejects_mismatched_headers() -> None:
    current = artifact("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    nxt = artifact("| x | y | z |\n| --- | --- | --- |\n| 3 | 4 | 5 |\n", page_no=2)

    outcome = run_operator(
        RepairOperator.MERGE_CROSS_PAGE_TABLE,
        current,
        defect(DefectCode.TABLE_CROSS_PAGE_BREAK),
        context(next_artifact=nxt),
    )

    assert outcome.changed is False


def test_coordinate_dependent_operators_fail_closed() -> None:
    """无坐标 / 需重跑的算子在 P0 诚实返回 unchanged，不伪造修复。"""
    original = artifact("正文\n")
    cases = {
        RepairOperator.REORDER_BLOCKS: DefectCode.READING_ORDER_BROKEN,
        RepairOperator.RESEGMENT_COLUMNS: DefectCode.COLUMN_MIXED,
        RepairOperator.ROTATE_PAGE: DefectCode.PAGE_ROTATED,
        RepairOperator.DESKEW: DefectCode.PAGE_ROTATED,
        RepairOperator.EXTRACT_REGION: DefectCode.REGION_EMPTY,
        RepairOperator.OCR_PAGE: DefectCode.ENCODING_LOSS,
    }

    for operator, code in cases.items():
        outcome = run_operator(operator, original, defect(code), context())
        assert outcome.changed is False
        assert outcome.candidate is None
        assert outcome.notes
