"""按需 `process_pdf` 标题标定（Phase 12 R3 / T138）。

`extract_pages_markdown` 的标题层级弱于文档级 `process_pdf`（同书 105 vs 199），
但 `process_pdf` 多页输出无页分隔、`pages` 为 1-indexed。本模块：

- 仅在乱码页或长文档无标题时触发（避免正常文档第二遍解析）；
- 调用 `process_pdf` 取标题行，按“规范化文本 + 页顺序”映射回逐页产物；
- 失败/无匹配时保留原产物并返回警告，不阻塞交付；
- 禁止逐页调用 `process_pdf`（无文档级收益且放大文件打开次数）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pdf_inspector

from dataagent.contracts.artifacts import PageArtifact

MIN_TEXT_PAGES_FOR_HEADING_CHECK: Final[int] = 30
MAX_HEADING_CHARS: Final[int] = 80
MAX_HEADING_LINE_EXTRA: Final[int] = 20
CALIBRATED_WARNING: Final[str] = "heading_calibrated"
FAILED_WARNING: Final[str] = "heading_calibration_failed"

_HEADING_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"第[一二三四五六七八九十百零〇\d]+[章节]|[一二三四五六七八九十]+、"
)


@dataclass(frozen=True, slots=True)
class HeadingCalibration:
    applied: bool
    headings: int
    warnings: tuple[str, ...] = ()


def needs_calibration(
    page_reasons: Sequence[str | None],
    page_markdowns: Sequence[str],
    *,
    min_text_pages: int = MIN_TEXT_PAGES_FOR_HEADING_CHECK,
) -> bool:
    if any(reason == "suspected_garbled_text" for reason in page_reasons):
        return True
    text_pages = [markdown for markdown in page_markdowns if markdown.strip()]
    if len(text_pages) < min_text_pages:
        return False
    headings = sum(
        1
        for markdown in text_pages
        for line in markdown.splitlines()
        if line.strip().startswith("#")
    )
    return headings == 0


def calibrate_headings(
    source: str | Path | bytes, artifacts: Sequence[PageArtifact]
) -> tuple[tuple[PageArtifact, ...], HeadingCalibration]:
    pages = [artifact.page_no for artifact in artifacts]
    try:
        result = _process(source, pages)
    except Exception:
        return tuple(artifacts), HeadingCalibration(False, 0, (FAILED_WARNING,))

    headings = _parse_headings(result.markdown or "")
    if not headings:
        return tuple(artifacts), HeadingCalibration(
            False, 0, ("heading_calibration_no_headings",)
        )

    assignments = _assign(headings, artifacts)
    if not assignments:
        return tuple(artifacts), HeadingCalibration(
            False, 0, ("heading_calibration_no_match",)
        )

    by_page: dict[int, list[tuple[int, int, str]]] = {}
    for page_index, line_index, level, text in assignments:
        by_page.setdefault(page_index, []).append((line_index, level, text))

    calibrated = list(artifacts)
    for index, items in by_page.items():
        artifact = calibrated[index]
        calibrated[index] = artifact.model_copy(
            update={
                "markdown": _inject(artifact.markdown, items),
                "warnings": (*artifact.warnings, CALIBRATED_WARNING),
            }
        )
    return tuple(calibrated), HeadingCalibration(True, len(assignments), ())


def _process(source: str | Path | bytes, pages: list[int]) -> Any:
    if isinstance(source, bytes):
        return pdf_inspector.process_pdf_bytes(source, pages=pages)
    return pdf_inspector.process_pdf(str(source), pages=pages)


def _parse_headings(markdown: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        text = stripped.lstrip("#").strip()
        if text:
            headings.append((level, text))
    return headings


def _assign(
    headings: Sequence[tuple[int, str]], artifacts: Sequence[PageArtifact]
) -> list[tuple[int, int, int, str]]:
    page_lines = [
        [
            (index, _normalize(line))
            for index, line in enumerate(artifact.markdown.splitlines())
            if line.strip()
        ]
        for artifact in artifacts
    ]
    cursor = 0
    used: set[tuple[int, int]] = set()
    assignments: list[tuple[int, int, int, str]] = []
    for level, text in headings:
        if not _looks_like_heading(text):
            continue
        target = _normalize(text)
        found: tuple[int, int] | None = None
        for page_index in range(cursor, len(artifacts)):
            for line_index, line in page_lines[page_index]:
                if (page_index, line_index) in used:
                    continue
                if target == line or target in line:
                    found = (page_index, line_index)
                    break
            if found is not None:
                break
        if found is not None:
            page_index, line_index = found
            used.add(found)
            assignments.append((page_index, line_index, level, text))
            cursor = page_index
    return assignments


def _inject(markdown: str, items: Sequence[tuple[int, int, str]]) -> str:
    lines = markdown.splitlines()
    for line_index, level, text in items:
        stripped = lines[line_index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(stripped) > len(text) + MAX_HEADING_LINE_EXTRA:
            continue
        lines[line_index] = "#" * level + " " + stripped
    return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")


def _looks_like_heading(text: str) -> bool:
    if len(text) > MAX_HEADING_CHARS:
        return False
    if text.rstrip().endswith(("。", "；", "，")):
        return False
    return len(_HEADING_MARKER_RE.findall(text)) < 2


def _normalize(text: str) -> str:
    return " ".join(text.split())
