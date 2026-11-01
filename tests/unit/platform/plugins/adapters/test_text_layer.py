"""文本层探测与质量门测试（T130，Phase 12 R1）。

覆盖三类样本：正常文本层 / 隐藏 OCR 文本层（render mode 3）/ 纯图片无文本层；
质量门：字符数 ≥ 50、U+FFFD ≤ 1%、可打印率 ≥ 95%。
"""

from pathlib import Path

import pdf_inspector
import pytest

from dataagent.platform.plugins.adapters.text_layer import (
    MAX_BAD_RATIO,
    MIN_CHARS,
    MIN_PRINTABLE_RATIO,
    assess_text,
    probe_text_layer,
)

PAGE_W = 612
PAGE_H = 792


def build_pdf(pages: list[str], *, image_only: bool = False) -> bytes:
    objects: list[str] = []
    count = len(pages)
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(count))
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>")
    font_id = 3 + 2 * count
    for index, content in enumerate(pages):
        resources = (
            "/Resources << /XObject << /Im0 100 0 R >> >>"
            if image_only
            else f"/Resources << /Font << /F1 {font_id} 0 R >> >>"
        )
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"{resources} /Contents {4 + index * 2} 0 R >>"
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    if image_only:
        objects.append(
            "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 3 >>\n"
            "stream\n\xff\x00\x00\nendstream"
        )
    else:
        objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    document = "%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(document)
    document += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        document += f"{offset:010d} 00000 n \n"
    document += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    )
    return document.encode("latin-1")


def text_content(lines: list[str]) -> str:
    return "\n".join(
        f"BT /F1 11 Tf 72 {720 - index * 16} Td ({line}) Tj ET"
        for index, line in enumerate(lines)
    )


def invisible_content(lines: list[str]) -> str:
    return "\n".join(
        f"BT /F1 11 Tf 72 {720 - index * 16} Td 3 Tr ({line}) Tj ET"
        for index, line in enumerate(lines)
    )


def write(tmp_path: Path, name: str, pages: list[str], **kwargs: object) -> Path:
    path = tmp_path / name
    path.write_bytes(build_pdf(pages, **kwargs))  # type: ignore[arg-type]
    return path


def test_quality_thresholds_are_explicit() -> None:
    assert MIN_CHARS == 50
    assert MAX_BAD_RATIO == 0.01
    assert MIN_PRINTABLE_RATIO == 0.95


def test_normal_text_layer_is_usable(tmp_path: Path) -> None:
    lines = [f"Normal text line {index} with enough characters" for index in range(1, 6)]
    path = write(tmp_path, "normal.pdf", [text_content(lines)])

    report = probe_text_layer(path)

    page = report.pages[0]
    assert page.page_no == 1
    assert page.usable is True
    assert page.quality.chars >= MIN_CHARS
    assert page.quality.bad_chars == 0
    assert "Normal text line 1" in page.text


def test_invisible_text_layer_is_recovered(tmp_path: Path) -> None:
    """pdf-inspector 忽略不可见文本；PDFium 必须读出来。"""
    lines = [f"Invisible OCR line {index} for text layer probe" for index in range(1, 9)]
    path = write(tmp_path, "invisible.pdf", [invisible_content(lines)])

    extraction = pdf_inspector.extract_pages_markdown(str(path))
    assert extraction.pages[0].markdown == ""
    assert extraction.pages[0].needs_ocr is True

    report = probe_text_layer(path)
    assert report.pages[0].usable is True
    assert report.pages[0].quality.chars > 200
    assert "Invisible OCR line 1" in report.pages[0].text


def test_image_only_page_has_no_text_layer(tmp_path: Path) -> None:
    path = write(tmp_path, "scan.pdf", ["q 200 0 0 200 72 500 cm /Im0 Do Q"], image_only=True)

    report = probe_text_layer(path)

    assert report.pages[0].usable is False
    assert report.pages[0].text == ""
    assert report.pages[0].quality.reason == "too_short"
    assert report.usable_page_numbers == ()


def test_assess_text_rejects_short_text() -> None:
    quality = assess_text("too short")

    assert quality.usable is False
    assert quality.reason == "too_short"


def test_assess_text_rejects_garbled_text() -> None:
    text = "正常文字内容" * 10 + "�" * 5

    quality = assess_text(text)

    assert quality.usable is False
    assert quality.reason == "too_many_bad_chars"


def test_assess_text_rejects_non_printable_text() -> None:
    text = "\x01\x02\x03\x04" * 20

    quality = assess_text(text)

    assert quality.usable is False
    assert quality.reason == "low_printable_ratio"


def test_pages_subset_uses_internal_page_numbers(tmp_path: Path) -> None:
    pages = [
        text_content(["Page one text with enough characters to pass the probe quality gate"]),
        invisible_content(["Page two text with enough characters to pass"]),
        text_content(["Page three text with enough characters to pass the probe quality gate"]),
    ]
    path = write(tmp_path, "three.pdf", pages)

    report = probe_text_layer(path, pages=[1, 3])

    assert [page.page_no for page in report.pages] == [1, 3]
    assert report.usable_page_numbers == (1, 3)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        probe_text_layer(tmp_path / "missing.pdf")


REAL_BOOK = Path(
    "doc/test_pdf/中华人民共和国招标投标法律法规全书：含相关政策 "
    "(法律法规出版社法规中心) (Z-Library)(OCR).pdf"
)


@pytest.mark.skipif(not REAL_BOOK.exists(), reason="本地语料不存在")
def test_real_scanned_book_hidden_layer_is_usable() -> None:
    report = probe_text_layer(REAL_BOOK, pages=[51])

    page = report.pages[0]
    assert page.usable is True
    assert page.quality.chars > 500
