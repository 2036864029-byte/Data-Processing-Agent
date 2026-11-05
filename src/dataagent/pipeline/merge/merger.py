"""页级合并（B7/T111 / FR-017 / PRD 4.7.1）。

按页码重排 → 文档级校验。跨页表合并在结构层做
（`pipeline/structure/crosspage.py`），标题层级平滑在结构视图做
（`pipeline/output/structure_view.py`）。

缺口页保留占位：页码不跳过，占位标记内嵌（Markdown 可识别注释块）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.repair.gap import PageGap


@dataclass(frozen=True, slots=True)
class MergedPage:
    page_no: int
    markdown: str
    gap: bool = False
    extract_quality: float | None = None
    defect_codes: tuple[DefectCode, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    code: str
    message: str
    page_no: int | None = None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool = True
    findings: tuple[ValidationFinding, ...] = ()


class DocumentValidator(Protocol):
    def validate(self, doc_id: str, pages: list[MergedPage]) -> ValidationResult: ...


class NullValidator:
    def validate(self, doc_id: str, pages: list[MergedPage]) -> ValidationResult:
        return ValidationResult(ok=True)


@dataclass(frozen=True, slots=True)
class MergedDocument:
    doc_id: str
    pages: tuple[MergedPage, ...]
    validation: ValidationResult

    @property
    def markdown(self) -> str:
        return "\n".join(page.markdown for page in self.pages)

    @property
    def gap_pages(self) -> tuple[int, ...]:
        return tuple(page.page_no for page in self.pages if page.gap)


def merge_document(
    doc_id: str,
    artifacts: Sequence[PageArtifact],
    gaps: Sequence[PageGap],
    *,
    qualities: Mapping[int, float] | None = None,
    validator: DocumentValidator | None = None,
) -> MergedDocument:
    pages = _initial_pages(doc_id, artifacts, gaps, qualities or {})
    validator = validator if validator is not None else NullValidator()
    validation = validator.validate(doc_id, list(pages))
    return MergedDocument(doc_id=doc_id, pages=tuple(pages), validation=validation)


def _initial_pages(
    doc_id: str,
    artifacts: Sequence[PageArtifact],
    gaps: Sequence[PageGap],
    qualities: Mapping[int, float],
) -> list[MergedPage]:
    pages: dict[int, MergedPage] = {}
    for artifact in artifacts:
        if artifact.doc_id != doc_id:
            raise ValueError(f"产物 doc_id 不一致：{artifact.doc_id} != {doc_id}")
        if artifact.page_no in pages:
            raise ValueError(f"页码重复：{artifact.page_no}")
        pages[artifact.page_no] = MergedPage(
            page_no=artifact.page_no,
            markdown=artifact.markdown,
            extract_quality=qualities.get(artifact.page_no),
        )
    for gap in gaps:
        if gap.doc_id != doc_id:
            raise ValueError(f"缺口记录 doc_id 不一致：{gap.doc_id} != {doc_id}")
        if gap.page_no in pages:
            raise ValueError(f"页码重复（产物与缺口冲突）：{gap.page_no}")
        pages[gap.page_no] = MergedPage(
            page_no=gap.page_no,
            markdown=_gap_placeholder(gap),
            gap=True,
            extract_quality=qualities.get(gap.page_no),
            defect_codes=gap.defect_codes,
        )
    if not pages:
        return []
    return [pages[page_no] for page_no in sorted(pages)]


def _gap_placeholder(gap: PageGap) -> str:
    codes = ",".join(code.value for code in gap.defect_codes)
    return (
        f"<!-- dataagent:gap page={gap.page_no} codes={codes} "
        f"fallback={gap.fallback_used.value} -->\n"
        f"> 【缺口页 {gap.page_no}】内容未还原（{codes}），"
        "已保留占位以维持页码，请人工复核。\n"
    )
