"""恢复路径自动评测（Phase 12 R4）：已知真值的不可见文本层样本。

生成一个 render mode 3 的隐藏文本 PDF，交给 engine.pdf 抽取，
验证 `text_layer_recovered` 路径并计算 CER。
"""

from __future__ import annotations

from typing import Any

from _lib import GOLDEN_ROOT, character_error_rate, plain_text

from dataagent.contracts.artifacts import ExtractionResult
from dataagent.contracts.plugin import PluginContext
from dataagent.platform.plugins.adapters.engine_pdf import PLUGIN as PDF_PLUGIN

NL = chr(10)


def recovery_eval(*, cer_max: float = 0.05) -> dict[str, Any]:
    lines = [f"Invisible recovery line {index} for evaluation" for index in range(1, 9)]
    path = GOLDEN_ROOT / "recovery_probe.pdf"
    path.write_bytes(_invisible_pdf(lines))
    raw = PDF_PLUGIN.invoke(
        "pdf.text.extract",
        {"path": str(path), "pages": [1]},
        PluginContext(task_id="eval", doc_id="a" * 64),
    )
    artifact = ExtractionResult.model_validate(raw).pages[0]
    reference = NL.join(lines)
    cer = character_error_rate(reference, plain_text(artifact.markdown))
    recovered = (
        artifact.text_layer == "invisible_recovered" and artifact.needs_ocr is False
    )
    return {
        "recovered": recovered,
        "cer": round(cer, 4),
        "cer_max": cer_max,
        "pass": recovered and cer <= cer_max,
    }


def _invisible_pdf(lines: list[str]) -> bytes:
    content = NL.join(
        f"BT /F1 11 Tf 72 {720 - index * 16} Td 3 Tr ({line}) Tj ET"
        for index, line in enumerate(lines)
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content)} >>{NL}stream{NL}{content}{NL}endstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = "%PDF-1.4" + NL
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{number} 0 obj{NL}{body}{NL}endobj{NL}"
    xref = len(document)
    document += f"xref{NL}0 {len(objects) + 1}{NL}0000000000 65535 f {NL}"
    for offset in offsets:
        document += f"{offset:010d} 00000 n {NL}"
    document += (
        f"trailer{NL}<< /Size {len(objects) + 1} /Root 1 0 R >>{NL}"
        f"startxref{NL}{xref}{NL}%%EOF{NL}"
    )
    return document.encode("latin-1")
