"""抽取阶段：按批调用插件、回填抽取后 layout 与文本层恢复标记。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol

from dataagent.contracts.artifacts import ExtractionResult, PageArtifact
from dataagent.contracts.plugin import PluginContext
from dataagent.kernel.registry import PluginRegistry
from dataagent.pipeline.profile.writer import PageProfileWriter
from dataagent.pipeline.sniff.sniffer import Family
from dataagent.pipeline.structure.models import PageLayout
from dataagent.pipeline.table.geometry import PositionedText
from dataagent.pipeline.task.context import DocumentContext
from dataagent.platform.plugins.adapters.layout_probe import ProbedLayout, probe_layout

EXTRACT_CAPABILITIES: Final[Mapping[Family, str]] = {
    Family.PDF: "pdf.text.extract",
    Family.TABULAR: "tabular.text.extract",
    Family.TEXT: "text.text.extract",
}


class LayoutProbe(Protocol):
    def __call__(
        self, source: str, *, item_pages: list[int], role_pages: list[int]
    ) -> ProbedLayout: ...


class ExtractionService:
    def __init__(
        self,
        *,
        registry: PluginRegistry,
        profiles: PageProfileWriter,
        layout_probe: LayoutProbe | None = None,
    ) -> None:
        self._registry = registry
        self._profiles = profiles
        self._layout_probe: LayoutProbe = layout_probe or probe_layout

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

    def attach_layout(
        self,
        context: DocumentContext,
        extraction: ExtractionResult,
        pages: Sequence[int],
    ) -> None:
        """探测页级坐标项与结构角色，写入 DocumentContext 旁车（不碰 PageArtifact）。

        坐标项仅覆盖本批 `pages_with_tables` 页（成本门控）；结构元素覆盖本批全部页。
        """
        if context.routing.family is not Family.PDF:
            return
        page_set = set(pages)
        item_pages = sorted(page_set & set(extraction.pages_with_tables))
        context.table_pages.update(item_pages)
        try:
            probed = self._layout_probe(
                str(context.path),
                item_pages=item_pages,
                role_pages=sorted(page_set),
            )
        except Exception:
            return

        items_by_page: dict[int, list[PositionedText]] = {}
        for value in probed.items:
            items_by_page.setdefault(value.page, []).append(
                PositionedText(
                    text=value.text,
                    x=value.x,
                    y=value.y,
                    width=value.width,
                    height=value.height,
                    mcid=value.mcid,
                )
            )
        roles_by_page: dict[int, list[tuple[int, str]]] = {}
        for role in probed.roles:
            roles_by_page.setdefault(role.page, []).append((role.mcid, role.role))

        for page_no in sorted(page_set):
            context.layouts[page_no] = PageLayout(
                page_no=page_no,
                items=tuple(items_by_page.get(page_no, [])),
                roles=tuple(roles_by_page.get(page_no, [])),
            )

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
                self._profiles.apply_text_layer_recovery(context.record.doc_id, artifact.page_no)
        except KeyError:
            return
