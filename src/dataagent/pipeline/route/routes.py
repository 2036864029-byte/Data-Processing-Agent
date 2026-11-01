"""两级路由（FR-004）：一级边界与格式判定，二级仅在 PDF 家族内做页级路由。

- 一级：按 B2 家族判定（pdf / tabular / text / image / unknown）产出格式决策；
  PDF 家族 defer 到二级，其余家族直接得到链路（unknown → reject）。
- 二级：仅 PDF 家族调用 `pdf.classify`；逐页映射 PageProfile（路由段）→ RoutePolicy → 链路。
  非 PDF 家族**不得**经过 PDF 分类能力（结构性保证：只有 PDF 分支引用 classifier）。
- 每条决策带机器可读理由与决策时画像快照并落库，可事后解释（G2 原样保留 route_reason）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Any, Final, Protocol

from dataagent.contracts.artifacts import PdfClassification
from dataagent.contracts.plugin import PluginContext
from dataagent.contracts.policies import (
    ChainId,
    PageProfile,
    ProfileClass,
    ProfileLayout,
    RoutePolicy,
    RouteSuggestion,
)
from dataagent.kernel.registry import PluginRegistry
from dataagent.pipeline.sniff.sniffer import Family, SniffResult

_CLASSIFY_CAPABILITY: Final[str] = "pdf.classify"

_FIRST_LEVEL_CHAINS: Final[Mapping[Family, ChainId]] = {
    Family.TABULAR: ChainId.TABULAR,
    Family.TEXT: ChainId.TEXT,
    Family.IMAGE: ChainId.IMAGE,
    Family.UNKNOWN: ChainId.REJECT,
}


@unique
class RouteLevel(StrEnum):
    FORMAT = "format"
    PAGE = "page"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """单条路由决策：理由 + 决策时画像快照，可事后解释（FR-004）。"""

    doc_id: str
    level: RouteLevel
    page_no: int | None
    chain: ChainId | None
    reasons: tuple[str, ...]
    profile_snapshot: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "level": self.level.value,
            "page_no": self.page_no,
            "chain": None if self.chain is None else self.chain.value,
            "reasons": list(self.reasons),
            "profile_snapshot": dict(self.profile_snapshot),
        }


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    doc_id: str
    family: Family
    level1: RouteDecision
    page_decisions: tuple[RouteDecision, ...]
    classification: PdfClassification | None


class RouteDecisionStore(Protocol):
    def save(self, decision: RouteDecision) -> None: ...

    def for_doc(self, doc_id: str) -> list[RouteDecision]: ...


class InMemoryRouteDecisionStore:
    def __init__(self) -> None:
        self._by_doc: dict[str, list[RouteDecision]] = {}

    def save(self, decision: RouteDecision) -> None:
        self._by_doc.setdefault(decision.doc_id, []).append(decision)

    def for_doc(self, doc_id: str) -> list[RouteDecision]:
        return list(self._by_doc.get(doc_id, ()))


class PdfClassifier(Protocol):
    def classify(self, path: Path) -> PdfClassification: ...


class RegistryPdfClassifier:
    """经注册表调用 `pdf.classify`（G3：插件调用只经注册表，禁直接 import 适配器）。"""

    def __init__(self, registry: PluginRegistry, context: PluginContext) -> None:
        self._registry = registry
        self._context = context

    def classify(self, path: Path) -> PdfClassification:
        raw = self._registry.invoke(
            _CLASSIFY_CAPABILITY, {"path": str(path)}, self._context
        )
        return PdfClassification.model_validate(raw)


class TwoLevelRouter:
    def __init__(
        self,
        *,
        decisions: RouteDecisionStore,
        policy: RoutePolicy,
        classifier: PdfClassifier,
    ) -> None:
        self._decisions = decisions
        self._policy = policy
        self._classifier = classifier

    def route(
        self, *, doc_id: str, path: Path | str, sniff: SniffResult
    ) -> RoutingOutcome:
        level1 = self._format_decision(doc_id, sniff)
        self._decisions.save(level1)

        if sniff.family is not Family.PDF:
            return RoutingOutcome(
                doc_id=doc_id,
                family=sniff.family,
                level1=level1,
                page_decisions=(),
                classification=None,
            )

        classification = self._classifier.classify(Path(path))
        page_decisions = tuple(self._page_decisions(doc_id, classification))
        for decision in page_decisions:
            self._decisions.save(decision)
        return RoutingOutcome(
            doc_id=doc_id,
            family=sniff.family,
            level1=level1,
            page_decisions=page_decisions,
            classification=classification,
        )

    def _format_decision(self, doc_id: str, sniff: SniffResult) -> RouteDecision:
        reasons = [*sniff.reasons, f"family:{sniff.family.value}"]
        if sniff.extension_mismatch:
            reasons.append("extension_mismatch")
        return RouteDecision(
            doc_id=doc_id,
            level=RouteLevel.FORMAT,
            page_no=None,
            chain=_FIRST_LEVEL_CHAINS.get(sniff.family),
            reasons=tuple(reasons),
            profile_snapshot={"sniff": sniff.as_dict()},
        )

    def _page_decisions(
        self, doc_id: str, classification: PdfClassification
    ) -> list[RouteDecision]:
        ocr_pages = set(classification.pages_needing_ocr)
        table_pages = set(classification.pages_with_tables)
        column_pages = set(classification.pages_with_columns)
        reasons_by_page = {
            entry.page: tuple(entry.reasons)
            for entry in classification.ocr_reasons_by_page
        }

        decisions: list[RouteDecision] = []
        for page_no in range(1, classification.page_count + 1):
            route_reason = reasons_by_page.get(page_no, ())
            profile = PageProfile.model_validate(
                {
                    "doc_id": doc_id,
                    "page_no": page_no,
                    "class": ProfileClass(
                        pdf_type=classification.pdf_type,
                        class_confidence=classification.confidence,
                        route=(
                            RouteSuggestion.OCR
                            if page_no in ocr_pages
                            else RouteSuggestion.NATIVE
                        ),
                        route_reason=list(route_reason),
                    ),
                    "layout": ProfileLayout(
                        columns=2 if page_no in column_pages else 1,
                        has_table=page_no in table_pages,
                        is_complex=page_no in table_pages or page_no in column_pages,
                    ),
                }
            )
            chain = self._policy.route(profile)
            decisions.append(
                RouteDecision(
                    doc_id=doc_id,
                    level=RouteLevel.PAGE,
                    page_no=page_no,
                    chain=chain,
                    reasons=(
                        *route_reason,
                        f"route:{profile.route.value}",
                        f"pdf_type:{profile.pdf_type.value}",
                    ),
                    profile_snapshot=profile.model_dump(by_alias=True, mode="json"),
                )
            )
        return decisions
