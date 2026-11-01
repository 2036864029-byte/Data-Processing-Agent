"""engine.csv 插件适配器：CSV / TSV → `tabular.text.extract`。

manifest 契约接入（contracts/plugin-manifest.md）；产出不可变 artifact，
单逻辑页（page_no = 1）。分隔符可由参数指定，否则按样本自动探测。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from dataagent.contracts.artifacts import ExtractionResult, PageArtifact
from dataagent.contracts.ids import content_hash
from dataagent.contracts.plugin import PluginContext, PluginManifest

EXTRACT = "tabular.text.extract"
_DELIMITERS: Final[tuple[str, ...]] = (",", ";", "\t", "|")
_ENCODINGS: Final[tuple[str, ...]] = ("utf-8-sig", "utf-8", "gb18030")

MANIFEST = PluginManifest.model_validate(
    {
        "id": "engine.csv",
        "name": "CSV 解析插件",
        "version": "1.0.0",
        "api_version": 1,
        "kind": "engine",
        "provides": [
            {
                "capability": EXTRACT,
                "input": {"schema": "doc.bytes", "params": "delimiter"},
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


class EngineCsvPlugin:
    @property
    def manifest(self) -> PluginManifest:
        return MANIFEST

    def invoke(
        self, capability: str, payload: Mapping[str, Any], context: PluginContext
    ) -> Any:
        if capability != EXTRACT:
            raise KeyError(f"engine.csv 不提供能力 {capability!r}")
        text, encoding = _read_text(payload)
        delimiter = str(payload.get("delimiter") or _detect_delimiter(text))
        markdown = _to_markdown_table(text, delimiter)
        artifact = PageArtifact(
            doc_id=context.doc_id,
            page_no=1,
            engine=MANIFEST.ref,
            params_hash=_params_hash(delimiter, encoding),
            markdown=markdown,
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
            raise ValueError("csv 插件载荷必须提供 path 或 data")
        raw = Path(str(path)).read_bytes()
    for encoding in (str(payload.get("encoding") or ""), *_ENCODINGS):
        if not encoding:
            continue
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("无法确定 CSV 编码（已尝试 utf-8 / gb18030）")


def _detect_delimiter(text: str) -> str:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(_DELIMITERS))
    except csv.Error:
        return ","
    return dialect.delimiter


def _to_markdown_table(text: str, delimiter: str) -> str:
    rows = [
        row
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = [_line(padded[0]), _line(["---"] * width)]
    lines.extend(_line(row) for row in padded[1:])
    return "\n".join(lines) + "\n"


def _line(cells: list[str]) -> str:
    return "| " + " | ".join(_escape(cell) for cell in cells) + " |"


def _escape(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _params_hash(delimiter: str, encoding: str) -> str:
    return content_hash(json.dumps({"delimiter": delimiter, "encoding": encoding}))


PLUGIN = EngineCsvPlugin()
