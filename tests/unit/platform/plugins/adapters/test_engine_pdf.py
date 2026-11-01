"""engine.pdf 双 capability 测试（T085，B3）。

- `pdf.classify` 在不产内容前提下完成判定：`markdown = None`、`cmap_gaps` 恒为空；
- `pdf.text.extract` 产不可变 artifact；页码经 contracts/pages.py 唯一转换点；
- 仅 `deterministic` 允许结果缓存（缓存键 = 内容哈希 + 参数哈希 + id@version）。
"""

import types
from pathlib import Path
from typing import Any

import pdf_inspector
import pytest
from pydantic import ValidationError

from dataagent.contracts.artifacts import ExtractionResult, PdfClassification
from dataagent.contracts.ids import result_cache_key
from dataagent.contracts.plugin import Determinism, PluginContext, PluginKind
from dataagent.kernel.registry import PluginRegistry
from dataagent.platform.plugins.adapters.engine_pdf import PLUGIN as PDF_PLUGIN

CONTEXT = PluginContext(task_id="task-1", doc_id="a" * 64)


def build_pdf(pages: list[str]) -> bytes:
    """构造最小合法 PDF（每页一行 Helvetica 文本），供真实引擎解析。"""
    objects: list[str] = []
    count = len(pages)
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(count))
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>")
    font_id = 3 + 2 * count
    for index, text in enumerate(pages):
        content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {4 + index * 2} 0 R >>"
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
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


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    path.write_bytes(build_pdf(["Hello Page One", "Second page text"]))
    return path


def test_manifest_declares_both_capabilities() -> None:
    manifest = PDF_PLUGIN.manifest

    assert manifest.id == "engine.pdf"
    assert manifest.kind is PluginKind.ENGINE
    assert {signature.capability for signature in manifest.provides} == {
        "pdf.classify",
        "pdf.text.extract",
    }
    assert manifest.determinism is Determinism.DETERMINISTIC
    assert manifest.batching.supported is True
    assert manifest.batching.default_batch_pages == 32


def test_classify_produces_no_content(pdf_path: Path) -> None:
    """验收 2：classify 必须能在不产内容前提下完成判定。"""
    raw = PDF_PLUGIN.invoke("pdf.classify", {"path": str(pdf_path)}, CONTEXT)
    result = PdfClassification.model_validate(raw)

    assert result.pdf_type.value == "text_based"
    assert result.page_count == 2
    assert result.markdown is None
    assert result.cmap_gaps == ()
    assert result.pages_needing_ocr == ()


def test_classify_uses_detection_not_full_processing(
    pdf_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """classify 只调 detect_pdf；不得内部偷偷全量抽取。"""

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("classify 不得调用 process_pdf")

    monkeypatch.setattr(pdf_inspector, "process_pdf", forbidden)
    monkeypatch.setattr(pdf_inspector, "extract_pages_markdown", forbidden)

    raw = PDF_PLUGIN.invoke("pdf.classify", {"path": str(pdf_path)}, CONTEXT)

    assert PdfClassification.model_validate(raw).markdown is None


def test_extract_produces_immutable_artifacts(pdf_path: Path) -> None:
    raw = PDF_PLUGIN.invoke(
        "pdf.text.extract", {"path": str(pdf_path), "pages": [1, 2]}, CONTEXT
    )
    result = ExtractionResult.model_validate(raw)

    assert [artifact.page_no for artifact in result.pages] == [1, 2]
    assert result.pages[0].markdown.strip()
    assert result.pages[0].engine == PDF_PLUGIN.manifest.ref
    assert result.pages[0].doc_id == CONTEXT.doc_id

    with pytest.raises(ValidationError):
        result.pages[0].markdown = "tampered"  # type: ignore[misc]


def test_extract_converts_internal_pages_to_plugin_indices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内部 1-indexed → 插件 0-indexed 只在 contracts/pages.py 转换。"""
    seen: dict[str, Any] = {}

    def fake_extract_pages_markdown(
        path: str, pages: list[int] | None = None
    ) -> Any:
        seen["pages"] = pages
        return types.SimpleNamespace(
            pages=[
                types.SimpleNamespace(
                    page=index, markdown=f"page-{index}", needs_ocr=False, ocr_reason=None
                )
                for index in pages or []
            ],
            pages_with_tables=[],
            pages_with_columns=[],
            pages_needing_ocr=[],
            ocr_reasons_by_page=[],
            is_complex=False,
        )

    monkeypatch.setattr(
        pdf_inspector, "extract_pages_markdown", fake_extract_pages_markdown
    )

    raw = PDF_PLUGIN.invoke(
        "pdf.text.extract",
        {"path": str(tmp_path / "x.pdf"), "pages": [1, 3, 5]},
        CONTEXT,
    )
    result = ExtractionResult.model_validate(raw)

    assert seen["pages"] == [0, 2, 4]
    assert [artifact.page_no for artifact in result.pages] == [1, 3, 5]
    assert [artifact.markdown for artifact in result.pages] == [
        "page-0",
        "page-2",
        "page-4",
    ]


def test_only_deterministic_plugins_are_cacheable() -> None:
    """宪法 I：仅 deterministic 允许结果缓存。"""
    registry = PluginRegistry()
    registry.register(PDF_PLUGIN)

    assert registry.cacheable("pdf.classify") is True
    assert registry.cacheable("pdf.text.extract") is True

    stochastic = _StochasticPlugin()
    registry.register(stochastic)

    assert registry.cacheable("other.classify") is False


def test_result_cache_key_composition() -> None:
    key = result_cache_key("doc-hash", "params-hash", "engine.pdf@1.0.0")

    assert key == "doc-hash:params-hash:engine.pdf@1.0.0"


def _StochasticPlugin() -> Any:
    from dataagent.contracts.plugin import (
        CapabilitySignature,
        IOSchema,
        Latency,
        PluginManifest,
    )

    manifest = PluginManifest(
        id="other.engine",
        name="test",
        version="1.0.0",
        api_version=1,
        kind=PluginKind.ENGINE,
        provides=[
            CapabilitySignature(
                capability="other.classify",
                input=IOSchema(schema="doc.bytes"),
                output=IOSchema(schema="other.v1"),
                latency=Latency(p50_ms=1, unit="document"),
            )
        ],
        determinism=Determinism.STOCHASTIC,
    )

    class _Plugin:
        @property
        def manifest(self) -> PluginManifest:
            return manifest

        def invoke(
            self, capability: str, payload: Any, context: PluginContext
        ) -> Any:
            return {}

    return _Plugin()
