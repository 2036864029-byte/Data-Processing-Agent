"""RoutePolicy 规则实现：PageProfile 的 class / layout → 链路 ID（PRD 4.2.2）。

页级决策，必须确定性（宪法 I）；`route` 建议来自 pdf.classify 的逐页路由，
本规则只做封闭集合映射，不做任何推断或模型调用。
"""

from __future__ import annotations

from dataagent.contracts.policies import ChainId, PageProfile, RouteSuggestion


class RuleRoutePolicy:
    """逐页链路路由：native → pdf.native；ocr → pdf.ocr；vlm → pdf.vlm。"""

    def route(self, profile: PageProfile) -> ChainId:
        if profile.route is RouteSuggestion.OCR:
            return ChainId.PDF_OCR
        if profile.route is RouteSuggestion.VLM:
            return ChainId.PDF_VLM
        return ChainId.PDF_NATIVE
