"""engine.pdf 插件适配器：pdf-inspector 双 capability（分类 / 抽取）。

契约依据 contracts/plugin-manifest.md + doc/pdf-inspector.md：
- `pdf.classify` 只调 `detect_pdf`（不产内容：markdown 恒 None、cmap_gaps 恒为空）。
- `pdf.text.extract` 调 `extract_pages_markdown`，一次传入多页、保持调用方顺序；
  产出不可变 artifact。
- 页码字段级转换只经 `contracts/pages.py`：内部 1-indexed ⇄ 插件 0-indexed。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pdf_inspector

from dataagent.contracts import pages
from dataagent.contracts.artifacts import (
    ExtractionResult,
    FontCMapGaps,
    PageArtifact,
    PageOcrReason,
    PdfClassification,
)
from dataagent.contracts.ids import content_hash
from dataagent.contracts.plugin import PluginContext, PluginManifest
from dataagent.contracts.policies import PdfType

CLASSIFY = "pdf.classify"
EXTRACT = "pdf.text.extract"

MANIFEST = PluginManifest.model_validate(
    {
        "id": "engine.pdf",
        "name": "PDF 解析与分类插件",
        "version": "1.0.0",
        "api_version": 1,
        "kind": "engine",
        "provides": [
            {
                "capability": CLASSIFY,
                "input": {"schema": "doc.bytes"},
                "output": {"schema": "pdf.classification.v1"},
                "latency": {"p50_ms": 25, "unit": "document"},
            },
            {
                "capability": EXTRACT,
                "input": {"schema": "doc.bytes", "params": "page_range"},
                "output": {"schema": "page.artifact.v1"},
                "latency": {"p50_ms": 16, "unit": "page"},
            },
        ],
        "cost_model": {
            "unit": "page",
            "classify_ms_p50": 25,
            "cpu_ms_p50": 16,
            "cpu_ms_p95": 40,
            "gpu_ms": 0,
            "memory_mb": 200,
        },
        "determinism": "deterministic",
        "concurrency": "reentrant",
        "trust": "builtin",
        "sandbox": "none",
        "limits": {"max_page_size_mb": 50, "timeout_s": 30},
        "batching": {
            "supported": True,
            "default_batch_pages": 32,
            "max_batch_pages": 50,
            "batch_affinity": "document",
        },
        "optional_deps": [
            {"name": "pdfium", "required_when": ["pdf.ocr"]},
            {"name": "onnxruntime", "required_when": ["pdf.ocr"]},
        ],
        "compatibility": {"requires": [], "conflicts": []},
    }
)


class EnginePdfPlugin:
    """同进程插件（sandbox: none，热路径直接调用）。"""

    @property
    def manifest(self) -> PluginManifest:
        return MANIFEST

    def invoke(
        self, capability: str, payload: Mapping[str, Any], context: PluginContext
    ) -> Any:
        if capability == CLASSIFY:
            return self._classify(payload)
        if capability == EXTRACT:
            return self._extract(payload, context)
        raise KeyError(f"engine.pdf 不提供能力 {capability!r}")

    def _classify(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = _call_source(
            pdf_inspector.detect_pdf, pdf_inspector.detect_pdf_bytes, payload
        )
        classification = PdfClassification(
            pdf_type=PdfType(result.pdf_type),
            confidence=float(result.confidence),
            page_count=int(result.page_count),
            pages_needing_ocr=tuple(int(page) for page in result.pages_needing_ocr),
            ocr_reasons_by_page=tuple(
                PageOcrReason(page=int(reason.page), reasons=tuple(reason.reasons))
                for reason in result.ocr_reasons_by_page
            ),
            pages_with_tables=tuple(int(page) for page in result.pages_with_tables),
            pages_with_columns=tuple(int(page) for page in result.pages_with_columns),
            is_complex_layout=bool(result.is_complex_layout),
            has_encoding_issues=bool(result.has_encoding_issues),
            cmap_gaps=tuple(
                FontCMapGaps(
                    font=gap.font,
                    codes=int(gap.codes),
                    interpolated=int(gap.interpolated),
                    unmapped=int(gap.unmapped),
                )
                for gap in result.cmap_gaps
            ),
            markdown=None,
        )
        return classification.model_dump(mode="json")

    def _extract(
        self, payload: Mapping[str, Any], context: PluginContext
    ) -> dict[str, Any]:
        internal_pages = payload.get("pages")
        plugin_pages = (
            None
            if internal_pages is None
            else pages.plugin_page_indices([int(page) for page in internal_pages])
        )
        result = _call_source(
            pdf_inspector.extract_pages_markdown,
            pdf_inspector.extract_pages_markdown_bytes,
            payload,
            pages=plugin_pages,
        )
        params_hash = _params_hash(payload)
        page_numbers = pages.internal_page_numbers(
            [int(page.page) for page in result.pages]
        )
        artifacts = tuple(
            PageArtifact(
                doc_id=context.doc_id,
                page_no=page_no,
                engine=MANIFEST.ref,
                params_hash=params_hash,
                markdown=page.markdown,
                needs_ocr=bool(page.needs_ocr),
                ocr_reason=page.ocr_reason,
            )
            for page_no, page in zip(page_numbers, result.pages, strict=True)
        )
        extraction = ExtractionResult(
            doc_id=context.doc_id,
            engine=MANIFEST.ref,
            params_hash=params_hash,
            pages=artifacts,
            pages_with_tables=tuple(int(page) for page in result.pages_with_tables),
            pages_with_columns=tuple(int(page) for page in result.pages_with_columns),
            pages_needing_ocr=tuple(int(page) for page in result.pages_needing_ocr),
            ocr_reasons_by_page=tuple(
                PageOcrReason(page=int(reason.page), reasons=tuple(reason.reasons))
                for reason in result.ocr_reasons_by_page
            ),
            is_complex=bool(result.is_complex),
        )
        return extraction.model_dump(mode="json")


def _call_source(
    path_call: Any, bytes_call: Any, payload: Mapping[str, Any], **kwargs: Any
) -> Any:
    """按载荷选择路径或字节入口（插件接口不读业务存储，由调用方给内容）。"""
    data = payload.get("data")
    if data is not None:
        return bytes_call(bytes(data), **kwargs)
    path = payload.get("path")
    if path is None:
        raise ValueError("pdf 插件载荷必须提供 path 或 data")
    return path_call(str(path), **kwargs)


def _params_hash(payload: Mapping[str, Any]) -> str:
    """参数指纹：只覆盖影响结果的参数（页范围显式参数）。"""
    if "params_hash" in payload:
        return str(payload["params_hash"])
    internal_pages = payload.get("pages")
    params = {"pages": None if internal_pages is None else list(internal_pages)}
    return content_hash(json.dumps(params, sort_keys=True))


PLUGIN = EnginePdfPlugin()
