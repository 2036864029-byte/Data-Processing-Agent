"""抽取阶段：按批调用插件、回填抽取后 layout 与文本层恢复标记。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from dataagent.contracts.artifacts import ExtractionResult, PageArtifact
from dataagent.contracts.plugin import PluginContext
from dataagent.kernel.registry import PluginRegistry
from dataagent.pipeline.profile.writer import PageProfileWriter
from dataagent.pipeline.sniff.sniffer import Family
from dataagent.pipeline.task.context import DocumentContext

EXTRACT_CAPABILITIES: Final[Mapping[Family, str]] = {
    Family.PDF: "pdf.text.extract",
    Family.TABULAR: "tabular.text.extract",
    Family.TEXT: "text.text.extract",
}


class ExtractionService:
    def __init__(
        self,
        *,
        registry: PluginRegistry,
        profiles: PageProfileWriter,
    ) -> None:
        self._registry = registry
        self._profiles = profiles

    def supports(self, family: Family) -> bool:
        return family in EXTRACT_CAPABILITIES

    def extract(
        self,
        context: DocumentContext,
        pages: Sequence[int],
        *,
        task_id: str,
    ) -> ExtractionResult:
        capability = EXTRACT_CAPABILITIES[context.routing.family]
        payload: dict[str, Any] = {"path": str(context.path), "pages": list(pages)}
        raw = self._registry.invoke(
            capability,
            payload,
            PluginContext(task_id=task_id, doc_id=context.record.doc_id),
        )
        return ExtractionResult.model_validate(raw)

    def backfill_layout(
        self,
        context: DocumentContext,
        artifact: PageArtifact,
        extraction: ExtractionResult,
    ) -> None:
        if context.routing.family is not Family.PDF:
            return
        try:
            self._profiles.write_extraction_layout(
                context.record.doc_id,
                artifact.page_no,
                has_table=artifact.page_no in extraction.pages_with_tables,
                columns=2 if artifact.page_no in extraction.pages_with_columns else 1,
                is_complex=extraction.is_complex
                or artifact.page_no in extraction.pages_with_tables
                or artifact.page_no in extraction.pages_with_columns,
            )
            if "text_layer_recovered" in artifact.warnings:
                self._profiles.apply_text_layer_recovery(
                    context.record.doc_id, artifact.page_no
                )
        except KeyError:
            return
