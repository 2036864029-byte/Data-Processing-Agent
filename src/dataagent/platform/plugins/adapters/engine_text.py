"""engine.text 插件适配器：纯文本 → `text.text.extract`。

manifest 契约接入（contracts/plugin-manifest.md）；内容原样保留（不做任何改写），
单逻辑页（page_no = 1），产出不可变 artifact。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from dataagent.contracts.artifacts import ExtractionResult, PageArtifact
from dataagent.contracts.ids import content_hash
from dataagent.contracts.plugin import PluginContext, PluginManifest

EXTRACT = "text.text.extract"
_ENCODINGS: Final[tuple[str, ...]] = ("utf-8-sig", "utf-8", "gb18030")

MANIFEST = PluginManifest.model_validate(
    {
        "id": "engine.text",
        "name": "纯文本解析插件",
        "version": "1.0.0",
        "api_version": 1,
        "kind": "engine",
        "provides": [
            {
                "capability": EXTRACT,
                "input": {"schema": "doc.bytes"},
                "output": {"schema": "page.artifact.v1"},
                "latency": {"p50_ms": 2, "unit": "document"},
            }
        ],
        "cost_model": {"unit": "page", "cpu_ms_p50": 2, "cpu_ms_p95": 5},
        "determinism": "deterministic",
        "concurrency": "reentrant",
        "trust": "builtin",
        "sandbox": "none",
        "limits": {"max_page_size_mb": 50, "timeout_s": 30},
        "batching": {"supported": False},
        "compatibility": {"requires": [], "conflicts": []},
    }
)


class EngineTextPlugin:
    @property
    def manifest(self) -> PluginManifest:
        return MANIFEST

    def invoke(
        self, capability: str, payload: Mapping[str, Any], context: PluginContext
    ) -> Any:
        if capability != EXTRACT:
            raise KeyError(f"engine.text 不提供能力 {capability!r}")
        text, encoding = _read_text(payload)
        artifact = PageArtifact(
            doc_id=context.doc_id,
            page_no=1,
            engine=MANIFEST.ref,
            params_hash=content_hash(json.dumps({"encoding": encoding})),
            markdown=text,
        )
        extraction = ExtractionResult(
            doc_id=context.doc_id,
            engine=MANIFEST.ref,
            params_hash=artifact.params_hash,
            pages=(artifact,),
        )
        return extraction.model_dump(mode="json")


def _read_text(payload: Mapping[str, Any]) -> tuple[str, str]:
    data = payload.get("data")
    if data is not None:
        raw = bytes(data)
    else:
        path = payload.get("path")
        if path is None:
            raise ValueError("text 插件载荷必须提供 path 或 data")
        raw = Path(str(path)).read_bytes()
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("无法确定文本编码（已尝试 utf-8 / gb18030）")


PLUGIN = EngineTextPlugin()
