"""L1 全量逐页质检（B5 / FR-009）：确定性指标 + 硬门控 + 定位到页。

口径逐字引用 contracts/qc-output.md「指标口径（L1 逐页，全量）」：
char_density / glyph_coverage / cid_unmapped_ratio / empty_region_ratio /
table_cell_fill / row_col_consistency / reading_order_score / bbox_coverage /
header_footer_leak / page_number_ok；`extract_quality` 为加权合成（非第 11 个指标）。

- L1 **始终全量**：`L1Checker` 不接收性能档位（profile_speed 只影响 L2 抽样）。
- QC 只读：输入只有指标，没有文件 / 产物引用，不重新解析、不修改产物。
- 每条缺陷带 `location`（page_no 必填），无算子的缺陷仅统计、不进修复队列。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from dataagent.contracts.defects import DEFECT_SPECS, Defect, DefectLocation
from dataagent.contracts.errors import DefectCode
from dataagent.contracts.messages import PageReport, Verdict
from dataagent.contracts.policies import AttributionLabel, PdfType
from dataagent.pipeline.quality.table_metrics import (
    COL_OCCUPANCY_MIN,
    ROW_COL_VARIANCE_MAX,
    ROW_OCCUPANCY_MIN,
    TABLE_CELL_FILL_MIN,
)
from dataagent.pipeline.quality.text_metrics import text_quality_metrics

GLYPH_COVERAGE_MIN: Final[float] = 0.95
CID_UNMAPPED_RATIO_MAX: Final[float] = 0.01
EMPTY_REGION_RATIO_MAX: Final[float] = 0.30
TABLE_PAGE_DENSITY_MIN: Final[float] = 0.5
COLUMN_PAGE_DENSITY_MIN: Final[float] = 0.4
READING_ORDER_SCORE_MIN: Final[float] = 0.88
HEADER_FOOTER_LEAK_MAX: Final[float] = 0.05

# 黄金集标定（2026-11-08，质量修复 Phase 4.2）：`char_density = 非空白字符数 / 468cm²`。
# 旧值 2.0 / 1.0 会把版面稀疏但有效页面（多栏、图表、问答版式）判成缺口——
# R-0077 1.064、R-0098 0.726、R-0102 0.87 均为人工判定合格页。
# 新值取“仍能拦住近空白页（<0.2）”的下限；纯扫描页字符数为 0，依旧必然命中。
CHAR_DENSITY_MIN_BY_TYPE: Final[Mapping[PdfType, float]] = {
    PdfType.TEXT_BASED: 0.9,
    PdfType.MIXED: 0.5,
    PdfType.SCANNED: 0.2,
    PdfType.IMAGE_BASED: 0.2,
}

# extract_quality 加权合成权重：由 60 页黄金集按生产分量拟合（全量拟合，
# 2026-11-06；见 tests/eval/calibrate.py 与 docs/b9-calibration-diagnosis.md）。
# 缺失指标按可用权重归一化；表格指标在非表格页取中性 1.0，避免把“没有表格”
# 当成表格质量差。cid / reading_order / bbox 等指标 P0 尚未产出，权重暂为 0，
# 接入后重新标定。
_QUALITY_WEIGHTS: Final[Mapping[str, float]] = {
    "glyph_coverage": 0.249,
    "char_density": 0.200,
    "clean_spacing": 0.143,
    "prose_ratio": 0.112,
    "numbering_ratio": 0.146,
    "table_cell_fill": 0.150,
    "cid": 0.0,
    "empty_region": 0.0,
    "reading_order": 0.0,
    "bbox_coverage": 0.0,
}

_ATTRIBUTION: Final[Mapping[DefectCode, AttributionLabel]] = {
    DefectCode.ENCODING_LOSS: AttributionLabel.ENCODING_BROKEN,
    DefectCode.LOW_DENSITY: AttributionLabel.LOW_DENSITY,
    DefectCode.REGION_EMPTY: AttributionLabel.LOW_DENSITY,
    DefectCode.TABLE_STRUCTURE_BROKEN: AttributionLabel.TABLE_BREAK,
    DefectCode.TABLE_CROSS_PAGE_BREAK: AttributionLabel.TABLE_BREAK,
    DefectCode.READING_ORDER_BROKEN: AttributionLabel.READING_ORDER,
    DefectCode.HEADER_FOOTER_LEAK: AttributionLabel.FORMAT_INVALID,
    DefectCode.PAGE_ROTATED: AttributionLabel.ROTATION,
}


@dataclass(frozen=True, slots=True)
class L1Metrics:
    """L1 逐页指标；None = 该指标当前不可得（不参与门控与质量合成）。"""

    char_density: float | None = None
    glyph_coverage: float | None = None
    cid_unmapped_ratio: float | None = None
    empty_region_ratio: float | None = None
    table_cell_fill: float | None = None
    # 契约口径：每行列数方差（越小越好）
    row_col_consistency: float | None = None
    table_header_ok: bool | None = None
    row_occupancy: float | None = None
    col_occupancy: float | None = None
    reading_order_score: float | None = None
    bbox_coverage: float | None = None
    header_footer_leak: float | None = None
    page_number_ok: bool | None = None
    # 产物文本质量（与黄金集标定同一口径，见 text_metrics.py）
    clean_spacing: float | None = None
    prose_ratio: float | None = None
    numbering_ratio: float | None = None

    @classmethod
    def from_text(cls, text: str, *, text_area: float) -> L1Metrics:
        """从恢复文本构造可用的 L1 指标（字符密度 / 字形覆盖 / 文本质量）。"""
        chars = [char for char in text if not char.isspace()]
        count = len(chars)
        bad = text.count("�")
        density = count / text_area if text_area > 0 else 0.0
        coverage = 1 - bad / count if count else 0.0
        quality = text_quality_metrics(text)
        return cls(
            char_density=density,
            glyph_coverage=coverage,
            clean_spacing=quality.clean_spacing,
            prose_ratio=quality.prose_ratio,
            numbering_ratio=quality.numbering_ratio,
        )


@dataclass(frozen=True, slots=True)
class L1PageInput:
    """单页质检输入：只有指标与页型，不含文件 / 产物（QC 只读）。"""

    doc_id: str
    page_no: int
    metrics: L1Metrics
    pdf_type: PdfType = PdfType.TEXT_BASED
    has_table: bool = False
    has_columns: bool = False
    rotation: int = 0
    table_index: int | None = None
    bbox: tuple[float, float, float, float] | None = None


class L1Checker:
    """逐页确定性质检；同一输入两次调用结果一致（FR-008 / 宪法 I）。"""

    def check(self, page: L1PageInput) -> PageReport:
        defects = self._defects(page)
        return PageReport(
            doc_id=page.doc_id,
            page_no=page.page_no,
            verdict=Verdict.FAIL if defects else Verdict.PASS,
            extract_quality=self._extract_quality(page),
            defects=list(defects),
            attribution=self._attribution(defects),
            recommended_action=defects[0].suggested_action if defects else None,
            qc_confidence=self._qc_confidence(page),
        )

    def _defects(self, page: L1PageInput) -> tuple[Defect, ...]:
        metrics = page.metrics
        threshold = _density_threshold(page)
        defects: list[Defect] = []

        glyph_low = (
            metrics.glyph_coverage is not None
            and metrics.glyph_coverage < GLYPH_COVERAGE_MIN
        )
        cid_high = (
            metrics.cid_unmapped_ratio is not None
            and metrics.cid_unmapped_ratio > CID_UNMAPPED_RATIO_MAX
        )
        if glyph_low or cid_high:
            severity = 0.0
            if metrics.glyph_coverage is not None:
                severity = max(severity, 1.0 - metrics.glyph_coverage)
            if metrics.cid_unmapped_ratio is not None:
                severity = max(
                    severity,
                    min(metrics.cid_unmapped_ratio / CID_UNMAPPED_RATIO_MAX, 1.0),
                )
            defects.append(
                self._defect(
                    DefectCode.ENCODING_LOSS,
                    page,
                    {
                        "glyph_coverage": metrics.glyph_coverage,
                        "cid_unmapped_ratio": metrics.cid_unmapped_ratio,
                    },
                    severity,
                )
            )

        if metrics.char_density is not None and metrics.char_density < threshold:
            defects.append(
                self._defect(
                    DefectCode.LOW_DENSITY,
                    page,
                    {"char_density": metrics.char_density, "threshold": threshold},
                    min(1.0, 1.0 - metrics.char_density / threshold),
                )
            )

        if (
            metrics.empty_region_ratio is not None
            and metrics.empty_region_ratio > EMPTY_REGION_RATIO_MAX
        ):
            defects.append(
                self._defect(
                    DefectCode.REGION_EMPTY,
                    page,
                    {"empty_region_ratio": metrics.empty_region_ratio},
                    (metrics.empty_region_ratio - EMPTY_REGION_RATIO_MAX)
                    / (1.0 - EMPTY_REGION_RATIO_MAX),
                    bbox=True,
                )
            )

        if page.has_table:
            fill = metrics.table_cell_fill
            variance = metrics.row_col_consistency
            occupancy_unknown = (
                metrics.row_occupancy is None and metrics.col_occupancy is None
            )
            structure_incomplete = occupancy_unknown or (
                metrics.row_occupancy is not None
                and metrics.row_occupancy < ROW_OCCUPANCY_MIN
            ) or (
                metrics.col_occupancy is not None
                and metrics.col_occupancy < COL_OCCUPANCY_MIN
            )
            fill_bad = (
                fill is not None
                and fill < TABLE_CELL_FILL_MIN
                and structure_incomplete
            )
            variance_bad = variance is not None and variance > ROW_COL_VARIANCE_MAX
            header_bad = metrics.table_header_ok is False
            if fill_bad or variance_bad or header_bad:
                severity = 0.0
                if fill is not None:
                    severity = max(
                        severity, (TABLE_CELL_FILL_MIN - fill) / TABLE_CELL_FILL_MIN
                    )
                if variance is not None:
                    severity = max(
                        severity,
                        min(variance / (ROW_COL_VARIANCE_MAX * 4.0), 1.0),
                    )
                if header_bad:
                    severity = max(severity, 0.5)
                defects.append(
                    self._defect(
                        DefectCode.TABLE_STRUCTURE_BROKEN,
                        page,
                        {
                            "table_cell_fill": fill,
                            "row_col_variance": variance,
                            "row_occupancy": metrics.row_occupancy,
                            "col_occupancy": metrics.col_occupancy,
                            "table_header_ok": "false" if header_bad else None,
                        },
                        severity,
                        table=True,
                    )
                )

        if (
            metrics.reading_order_score is not None
            and metrics.reading_order_score < READING_ORDER_SCORE_MIN
        ):
            defects.append(
                self._defect(
                    DefectCode.READING_ORDER_BROKEN,
                    page,
                    {"reading_order_score": metrics.reading_order_score},
                    (READING_ORDER_SCORE_MIN - metrics.reading_order_score)
                    / READING_ORDER_SCORE_MIN,
                )
            )

        if (
            metrics.header_footer_leak is not None
            and metrics.header_footer_leak > HEADER_FOOTER_LEAK_MAX
        ):
            defects.append(
                self._defect(
                    DefectCode.HEADER_FOOTER_LEAK,
                    page,
                    {"header_footer_ratio": metrics.header_footer_leak},
                    (metrics.header_footer_leak - HEADER_FOOTER_LEAK_MAX)
                    / (1.0 - HEADER_FOOTER_LEAK_MAX),
                )
            )

        if page.rotation != 0:
            defects.append(
                self._defect(
                    DefectCode.PAGE_ROTATED,
                    page,
                    {"rotation": page.rotation},
                    min(1.0, abs(page.rotation) / 90.0),
                    angle=True,
                )
            )

        if metrics.page_number_ok is False:
            defects.append(
                self._defect(
                    DefectCode.PAGE_NUMBER_BROKEN,
                    page,
                    {"page_number_ok": "false"},
                    0.5,
                )
            )

        return tuple(defects)

    def _defect(
        self,
        code: DefectCode,
        page: L1PageInput,
        evidence: Mapping[str, float | int | str | None],
        severity: float,
        *,
        bbox: bool = False,
        table: bool = False,
        angle: bool = False,
    ) -> Defect:
        location = DefectLocation(
            page_no=page.page_no,
            table_index=page.table_index if table else None,
            bbox=page.bbox if bbox else None,
            angle=float(page.rotation) if angle else None,
        )
        return Defect(
            code=code,
            location=location,
            evidence={
                key: value for key, value in evidence.items() if value is not None
            },
            severity=_clamp(severity),
            suggested_action=DEFECT_SPECS[code].operator,
        )

    def _extract_quality(self, page: L1PageInput) -> float:
        metrics = page.metrics
        threshold = _density_threshold(page)
        parts: list[tuple[float, float]] = []

        def add(
            name: str, score: float | None, *, neutral: float | None = None
        ) -> None:
            weight = _QUALITY_WEIGHTS.get(name, 0.0)
            if weight <= 0:
                return
            value = score if score is not None else neutral
            if value is None:
                return
            parts.append((weight, _clamp(value)))

        add("glyph_coverage", metrics.glyph_coverage)
        if metrics.cid_unmapped_ratio is not None:
            add(
                "cid",
                1.0 - min(metrics.cid_unmapped_ratio / CID_UNMAPPED_RATIO_MAX, 1.0),
            )
        if metrics.char_density is not None:
            add("char_density", min(metrics.char_density / threshold, 1.0))
        if metrics.empty_region_ratio is not None:
            add("empty_region", 1.0 - min(metrics.empty_region_ratio, 1.0))
        add("reading_order", metrics.reading_order_score)
        add("bbox_coverage", metrics.bbox_coverage)
        add("clean_spacing", metrics.clean_spacing)
        add("prose_ratio", metrics.prose_ratio)
        add("numbering_ratio", metrics.numbering_ratio)
        add("table_cell_fill", metrics.table_cell_fill, neutral=1.0)
        if not parts:
            return 0.0
        total_weight = sum(weight for weight, _ in parts)
        return round(
            sum(weight * score for weight, score in parts) / total_weight, 6
        )

    def _qc_confidence(self, page: L1PageInput) -> float:
        metrics = page.metrics
        available = sum(
            1
            for value in (
                metrics.glyph_coverage,
                metrics.cid_unmapped_ratio,
                metrics.empty_region_ratio,
                metrics.char_density,
                metrics.page_number_ok,
            )
            if value is not None
        )
        if page.has_table:
            available += 1 if metrics.table_cell_fill is not None else 0
            return round(available / 6.0, 3)
        return round(available / 5.0, 3)

    def _attribution(self, defects: tuple[Defect, ...]) -> str | None:
        for defect in defects:
            label = _ATTRIBUTION.get(defect.code)
            if label is not None:
                return label.value
        return None


def defects_for_repair(report: PageReport) -> tuple[Defect, ...]:
    """修复队列只收有算子且带 location 的缺陷；其余仅统计（G5）。"""
    return tuple(
        defect
        for defect in report.defects
        if DEFECT_SPECS[defect.code].operator is not None
    )


def _density_threshold(page: L1PageInput) -> float:
    """字符密度阈值：页型分档，表格页 / 分栏页取更低的“稀疏但有效”阈值。"""
    threshold = CHAR_DENSITY_MIN_BY_TYPE[page.pdf_type]
    if page.has_table:
        threshold = min(threshold, TABLE_PAGE_DENSITY_MIN)
    if page.has_columns:
        threshold = min(threshold, COLUMN_PAGE_DENSITY_MIN)
    return threshold


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


L1_CHECKER: Final[L1Checker] = L1Checker()
