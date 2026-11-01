"""`process_pdf` 标题标定测试（T133，Phase 12 R3）。

- 只在乱码页 / 长文档无标题时触发；
- `process_pdf.pages` 为 1-indexed，按页顺序 + 规范化文本回填标题；
- 失败返回原产物并标记 `heading_calibration_failed`，不阻塞。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.plugin import PluginContext
from dataagent.platform.plugins.adapters import engine_pdf, heading_calibration
from dataagent.platform.plugins.adapters.engine_pdf import PLUGIN as PDF_PLUGIN
from dataagent.platform.plugins.adapters.heading_calibration import (
    HeadingCalibration,
    calibrate_headings,
    needs_calibration,
)


def artifact(page_no: int, markdown: str) -> PageArtifact:
    return PageArtifact(
        doc_id="a" * 64,
        page_no=page_no,
        engine="engine.pdf@1.0.0",
        params_hash="params",
        markdown=markdown,
    )


def test_trigger_on_suspected_garbled_page() -> None:
    assert needs_calibration(["suspected_garbled_text"], [""]) is True
    assert needs_calibration(["invisible_text_layer"], [""]) is False


def test_trigger_on_long_document_without_headings() -> None:
    pages = ["plain body text\n"] * 29
    assert needs_calibration([None] * 29, pages) is False

    pages.append("plain body text\n")
    assert needs_calibration([None] * 30, pages) is True

    pages[0] = "# Existing heading\nplain body\n"
    assert needs_calibration([None] * 30, pages) is False


def test_headings_are_mapped_by_text_and_page_order(monkeypatch) -> None:
    artifacts = (
        artifact(1, "第一章 总则\n正文A\n"),
        artifact(2, "第一节 范围\n正文B\n"),
    )

    def fake_process(path: str, pages: list[int] | None = None):
        return SimpleNamespace(
            markdown=(
                "# 第一章 总则\n\n正文A\n\n"
                "## 第一节 范围\n\n正文B\n"
            )
        )

    monkeypatch.setattr(
        heading_calibration.pdf_inspector, "process_pdf", fake_process
    )

    calibrated, outcome = calibrate_headings("book.pdf", artifacts)

    assert outcome == HeadingCalibration(applied=True, headings=2, warnings=())
    assert calibrated[0].markdown.startswith("# 第一章 总则")
    assert calibrated[1].markdown.startswith("## 第一节 范围")
    assert "heading_calibrated" in calibrated[0].warnings


def test_duplicate_headings_are_assigned_sequentially(monkeypatch) -> None:
    artifacts = (
        artifact(1, "重复标题\n正文一\n"),
        artifact(2, "重复标题\n正文二\n"),
    )

    def fake_process(path: str, pages: list[int] | None = None):
        return SimpleNamespace(markdown="# 重复标题\n\n正文一\n# 重复标题\n\n正文二\n")

    monkeypatch.setattr(
        heading_calibration.pdf_inspector, "process_pdf", fake_process
    )

    calibrated, outcome = calibrate_headings("book.pdf", artifacts)

    assert outcome.headings == 2
    assert calibrated[0].markdown.startswith("# 重复标题")
    assert calibrated[1].markdown.startswith("# 重复标题")


def test_process_uses_one_indexed_page_numbers(monkeypatch) -> None:
    artifacts = (artifact(5, "正文五\n"), artifact(6, "正文六\n"))
    calls: list[list[int] | None] = []

    def fake_process(path: str, pages: list[int] | None = None):
        calls.append(pages)
        return SimpleNamespace(markdown="正文五\n正文六\n")

    monkeypatch.setattr(
        heading_calibration.pdf_inspector, "process_pdf", fake_process
    )

    calibrate_headings("book.pdf", artifacts)

    assert calls == [[5, 6]]


def test_calibration_failure_returns_original(monkeypatch) -> None:
    artifacts = (artifact(1, "正文\n"),)

    def broken(path: str, pages: list[int] | None = None):
        raise RuntimeError("process failed")

    monkeypatch.setattr(
        heading_calibration.pdf_inspector, "process_pdf", broken
    )

    calibrated, outcome = calibrate_headings("book.pdf", artifacts)

    assert calibrated == artifacts
    assert outcome.applied is False
    assert outcome.warnings == ("heading_calibration_failed",)


def test_engine_injects_calibrated_headings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "page.pdf"
    path.write_bytes(
        _single_text_page("Plain body text long enough for extraction")
    )

    monkeypatch.setattr(engine_pdf, "needs_calibration", lambda *_: True)
    monkeypatch.setattr(
        engine_pdf,
        "calibrate_headings",
        lambda source, artifacts: (
            (
                artifacts[0].model_copy(
                    update={
                        "markdown": "# 标题\n" + artifacts[0].markdown,
                        "warnings": ("heading_calibrated",),
                    }
                ),
            ),
            HeadingCalibration(applied=True, headings=1, warnings=()),
        ),
    )

    raw = PDF_PLUGIN.invoke(
        "pdf.text.extract", {"path": str(path), "pages": [1]}, PluginContext("t", "a" * 64)
    )
    page = raw["pages"][0]

    assert page["markdown"].startswith("# 标题")
    assert "heading_calibrated" in page["warnings"]


def _single_text_page(text: str) -> bytes:
    content = f"BT /F1 11 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
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
