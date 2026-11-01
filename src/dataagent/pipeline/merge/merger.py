"""页级合并（B7/T111 / FR-017 / PRD 4.7.1）。

按页码重排 → 跨页表合并（表头列名相似度 > 0.8 + 列数一致 + 触页边界）
→ 标题层级重建 → 文档级校验。

缺口页保留占位：页码不跳过，占位标记内嵌（Markdown 可识别注释块）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final, Protocol

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.repair.gap import PageGap

HEADER_SIMILARITY_MIN: Final[float] = 0.8
_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^(#{1,6})\s+(.*)$")
_SEPARATOR_CELL_RE: Final[re.Pattern[str]] = re.compile(r":?-{3,}:?")


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
    pages = _merge_cross_page_tables(pages)
    pages = _normalize_headings(pages)
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


def _merge_cross_page_tables(pages: list[MergedPage]) -> list[MergedPage]:
    texts = [page.markdown for page in pages]
    target: int | None = None
    target_header: list[str] | None = None

    for index, page in enumerate(pages):
        if page.gap:
            target = None
            target_header = None
            continue

        lines = texts[index].splitlines()
        first = _first_table_at_start(lines)
        if first is not None and target is not None and target_header is not None:
            start, end = first
            header = _header_cells(lines[start])
            if len(header) == len(target_header) and _similar(header, target_header):
                body_start = start + 1
                if body_start < end and _is_separator(_split_cells(lines[body_start])):
                    body_start += 1
                body = lines[body_start:end]
                if body:
                    target_lines = texts[target].splitlines()
                    last = _last_table_at_end(target_lines)
                    if last is not None:
                        _, target_end = last
                        texts[target] = _join(
                            target_lines[:target_end] + body + target_lines[target_end:]
                        )
                        texts[index] = _join(lines[:start] + lines[end:])
                        updated = texts[index].splitlines()
                        new_last = _last_table_at_end(updated)
                        if new_last is not None:
                            target = index
                            target_header = _header_cells(updated[new_last[0]])
                        continue

        last = _last_table_at_end(lines)
        if last is not None:
            target = index
            target_header = _header_cells(lines[last[0]])
        else:
            target = None
            target_header = None

    return [
        MergedPage(
            page_no=page.page_no,
            markdown=texts[index],
            gap=page.gap,
            extract_quality=page.extract_quality,
            defect_codes=page.defect_codes,
        )
        for index, page in enumerate(pages)
    ]


def _normalize_headings(pages: list[MergedPage]) -> list[MergedPage]:
    previous_level = 0
    normalized: list[MergedPage] = []
    for page in pages:
        if page.gap:
            normalized.append(page)
            continue
        lines = page.markdown.splitlines()
        changed = False
        for index, line in enumerate(lines):
            match = _HEADING_RE.match(line)
            if match is None:
                continue
            level = len(match.group(1))
            new_level = min(level, previous_level + 1)
            previous_level = new_level
            if new_level != level:
                lines[index] = "#" * new_level + " " + match.group(2)
                changed = True
        normalized.append(
            MergedPage(
                page_no=page.page_no,
                markdown=_join(lines) if changed else page.markdown,
                gap=page.gap,
                extract_quality=page.extract_quality,
                defect_codes=page.defect_codes,
            )
        )
    return normalized


def _table_blocks(lines: list[str]) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        if not _is_table_line(lines[index]):
            index += 1
            continue
        start = index
        while index < len(lines) and _is_table_line(lines[index]):
            index += 1
        blocks.append((start, index))
    return blocks


def _first_table_at_start(lines: list[str]) -> tuple[int, int] | None:
    blocks = _table_blocks(lines)
    if not blocks:
        return None
    start, end = blocks[0]
    if any(line.strip() for line in lines[:start]):
        return None
    return start, end


def _last_table_at_end(lines: list[str]) -> tuple[int, int] | None:
    blocks = _table_blocks(lines)
    if not blocks:
        return None
    start, end = blocks[-1]
    if any(line.strip() for line in lines[end:]):
        return None
    return start, end


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def _split_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator(cells: list[str]) -> bool:
    non_empty = [cell for cell in cells if cell]
    return bool(non_empty) and all(
        _SEPARATOR_CELL_RE.fullmatch(cell) for cell in non_empty
    )


def _header_cells(line: str) -> list[str]:
    return [cell.casefold() for cell in _split_cells(line)]


def _similar(left: list[str], right: list[str]) -> bool:
    ratio = SequenceMatcher(None, "|".join(left), "|".join(right)).ratio()
    return ratio >= HEADER_SIMILARITY_MIN


def _join(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + "\n"
