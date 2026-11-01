"""engine.pdf 隐藏文本层恢复测试（T131，Phase 12 R1）。"""

from pathlib import Path

import pytest

from dataagent.contracts.artifacts import ExtractionResult
from dataagent.contracts.plugin import PluginContext
from dataagent.platform.plugins.adapters import engine_pdf
from dataagent.platform.plugins.adapters.engine_pdf import PLUGIN as PDF_PLUGIN

CONTEXT = PluginContext(task_id="task-1", doc_id="d" * 64)


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
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
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


def invisible_content(lines: list[str]) -> str:
    return "\n".join(
        f"BT /F1 11 Tf 72 {720 - index * 16} Td 3 Tr ({line}) Tj ET"
        for index, line in enumerate(lines)
    )


def text_content(lines: list[str]) -> str:
    return "\n".join(
        f"BT /F1 11 Tf 72 {720 - index * 16} Td ({line}) Tj ET"
        for index, line in enumerate(lines)
    )


def write(tmp_path: Path, name: str, pages: list[str], **kwargs: object) -> Path:
    path = tmp_path / name
    path.write_bytes(build_pdf(pages, **kwargs))  # type: ignore[arg-type]
    return path


def extract(path: Path) -> ExtractionResult:
    raw = PDF_PLUGIN.invoke(
        "pdf.text.extract", {"path": str(path), "pages": [1]}, CONTEXT
    )
    return ExtractionResult.model_validate(raw)


def test_invisible_text_page_is_recovered(tmp_path: Path) -> None:
    lines = [f"Invisible OCR line {index} for recovery test" for index in range(1, 9)]
    path = write(tmp_path, "invisible.pdf", [invisible_content(lines)])

    result = extract(path)
    artifact = result.pages[0]

    assert artifact.needs_ocr is False
    assert artifact.ocr_reason is None
    assert "Invisible OCR line 1" in artifact.markdown
    assert "text_layer_recovered" in artifact.warnings
    assert artifact.text_layer == "invisible_recovered"


def test_pure_scan_page_keeps_needs_ocr(tmp_path: Path) -> None:
    path = write(tmp_path, "scan.pdf", ["q 200 0 0 200 72 500 cm /Im0 Do Q"], image_only=True)

    artifact = extract(path).pages[0]

    assert artifact.needs_ocr is True
    assert artifact.markdown == ""
    assert artifact.text_layer == "absent"
    assert any("text_layer_unusable" in warning for warning in artifact.warnings)


def test_normal_text_page_does_not_probe_layer(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "normal.pdf",
        [text_content(["Normal page text with enough characters for extraction"])],
    )

    artifact = extract(path).pages[0]

    assert artifact.needs_ocr is False
    assert artifact.text_layer is None
    assert artifact.warnings == ()


def test_probe_failure_does_not_break_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path, "invisible.pdf", [invisible_content(["invisible " * 20])])

    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("probe failed")

    monkeypatch.setattr(engine_pdf, "probe_text_layer", broken)

    artifact = extract(path).pages[0]

    assert artifact.needs_ocr is True
    assert artifact.text_layer == "absent"
    assert any("probe_failed" in warning for warning in artifact.warnings)
