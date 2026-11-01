"""隐藏文本层探测与质量门（Phase 12 R1 / T135）。

pdf-inspector 忽略扫描件中的不可见文本层（render mode 3），
`extract_pages_markdown` / `process_pdf` 对这类页返回空；
PDFium 可读取该层。本模块用 `pypdfium2` 逐页读取并给出质量判定：

- 字符数 ≥ 50（非空白字符）
- `U+FFFD` 占比 ≤ 1%
- 可打印字符占比 ≥ 95%

达标则供适配器复用（不送 OCR）；不达标才进入 OCR / 增强器。
纯 PDFium 读取，无业务 IO；页码内部一律 1-indexed。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pypdfium2 as pdfium

MIN_CHARS: Final[int] = 50
MAX_BAD_RATIO: Final[float] = 0.01
MIN_PRINTABLE_RATIO: Final[float] = 0.95


@dataclass(frozen=True, slots=True)
class TextLayerQuality:
    chars: int
    bad_chars: int
    printable_ratio: float
    usable: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TextLayerPage:
    page_no: int
    text: str
    quality: TextLayerQuality

    @property
    def usable(self) -> bool:
        return self.quality.usable


@dataclass(frozen=True, slots=True)
class TextLayerReport:
    path: str
    pages: tuple[TextLayerPage, ...]

    def page(self, page_no: int) -> TextLayerPage | None:
        return next(
            (page for page in self.pages if page.page_no == page_no), None
        )

    @property
    def usable_page_numbers(self) -> tuple[int, ...]:
        return tuple(page.page_no for page in self.pages if page.usable)


def assess_text(text: str) -> TextLayerQuality:
    content = "".join(char for char in text if not char.isspace())
    chars = len(content)
    if chars < MIN_CHARS:
        return TextLayerQuality(chars, 0, 0.0, False, "too_short")

    bad_chars = text.count("�")
    printable = sum(1 for char in content if char.isprintable())
    printable_ratio = printable / chars
    bad_ratio = bad_chars / chars
    if bad_ratio > MAX_BAD_RATIO:
        return TextLayerQuality(
            chars, bad_chars, printable_ratio, False, "too_many_bad_chars"
        )
    if printable_ratio < MIN_PRINTABLE_RATIO:
        return TextLayerQuality(
            chars, bad_chars, printable_ratio, False, "low_printable_ratio"
        )
    return TextLayerQuality(chars, bad_chars, printable_ratio, True, None)


def probe_text_layer(
    source: str | Path | bytes, *, pages: Sequence[int] | None = None
) -> TextLayerReport:
    """读取指定页（内部 1-indexed）的隐藏文本层；未指定则整本。"""
    if isinstance(source, bytes):
        label = "<bytes>"
        document = pdfium.PdfDocument(source)
    else:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在：{path}")
        label = str(path)
        document = pdfium.PdfDocument(label)

    try:
        page_numbers = (
            list(pages) if pages is not None else list(range(1, len(document) + 1))
        )
        results: list[TextLayerPage] = []
        for page_no in page_numbers:
            if page_no < 1 or page_no > len(document):
                raise ValueError(f"页码越界：{page_no}（共 {len(document)} 页）")
            try:
                text = document[page_no - 1].get_textpage().get_text_range()
            except Exception:  # pragma: no cover - PDFium 异常按不可用处理
                text = ""
            results.append(
                TextLayerPage(
                    page_no=page_no, text=text, quality=assess_text(text)
                )
            )
        return TextLayerReport(path=label, pages=tuple(results))
    finally:
        document.close()
