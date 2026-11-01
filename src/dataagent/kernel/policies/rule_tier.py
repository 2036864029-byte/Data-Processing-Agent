"""TierPolicy 规则实现：PageProfile + extract_quality → T0–T3（PRD 4.3.5）。

成本阶梯从低到高（宪法 IV）：先判定（T0）→ 后处理（T1）→ 参数重跑（T2）→ 换引擎（T3）。
全部阈值是 P0 确定性默认值；上线前由黄金集标定（research D3）替换初值。
"""

from __future__ import annotations

from dataagent.contracts.policies import (
    TEXT_LAYER_RECOVERED_REASON,
    PageProfile,
    PdfType,
    RouteSuggestion,
    Tier,
)

TAU0: float = 0.85
CLASS_CONFIDENCE_MIN: float = 0.85
GLYPH_COVERAGE_MIN: float = 0.95
HEADER_FOOTER_RATIO_MAX: float = 0.05


class RuleTierPolicy:
    """四档兜底判定：同一输入两次判定结果一致（FR-008）。"""

    def __init__(
        self,
        *,
        tau0: float = TAU0,
        class_confidence_min: float = CLASS_CONFIDENCE_MIN,
        glyph_coverage_min: float = GLYPH_COVERAGE_MIN,
        header_footer_ratio_max: float = HEADER_FOOTER_RATIO_MAX,
    ) -> None:
        self._tau0 = tau0
        self._class_confidence_min = class_confidence_min
        self._glyph_coverage_min = glyph_coverage_min
        self._header_footer_ratio_max = header_footer_ratio_max

    def tier(self, profile: PageProfile, quality: float) -> Tier:
        recovered = TEXT_LAYER_RECOVERED_REASON in profile.class_.route_reason
        # T3：插件能力边界外（非 native 链路 / 扫描件 / 分类判不准 / 编码全损）
        # 隐藏文本层恢复页除外：文本可用时按质量与版式正常判档
        if not recovered and (
            profile.route is not RouteSuggestion.NATIVE
            or profile.pdf_type in {PdfType.SCANNED, PdfType.IMAGE_BASED}
            or profile.class_confidence < self._class_confidence_min
        ):
            return Tier.T3
        coverage = profile.glyph_coverage
        if coverage is not None and coverage < self._glyph_coverage_min:
            return Tier.T3

        # T1：文本层完好但结构有问题（表格 / 页眉页脚）——纯 CPU 后处理
        header_footer_ratio = profile.header_footer_ratio
        if profile.has_table or (
            header_footer_ratio is not None
            and header_footer_ratio > self._header_footer_ratio_max
        ):
            return Tier.T1

        # T2：参数不当（分栏 / 复杂版面 / 旋转）或质量不达标
        if (
            profile.columns > 1
            or profile.is_complex
            or profile.rotation != 0
            or quality < self._tau0
        ):
            return Tier.T2

        return Tier.T0
