"""extract_quality 标定（B9/T123）：与生产 L1 完全同一口径。

输入：`var/golden/annotations.csv`（60 页人工子集）+ `manifest.csv` + `parsed/*.md`。
特征直接取 `L1Checker` 实际使用的分量分数，避免“标定用一套、生产用另一套”：

- glyph_coverage / char_density（按页型阈值归一）
- clean_spacing / prose_ratio / numbering_ratio（`text_metrics.py`）
- table_cell_fill（非表格页取中性 1.0，不把“没有表格”当成表格质量差）

流程：分层 2/3 拟合、1/3 留出 + 5 折分层交叉验证；无截距非负最小二乘拟合权重，
阈值扫描最大化 `needs_manual` 一致率；报告 Spearman、一致率与 QC precision/recall。
没有人工标注时输出 `insufficient_labels`（不阻塞 CI）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from _lib import (
    GOLDEN_ROOT,
    best_agreement_threshold,
    fit_nonnegative_weights,
    read_csv,
    spearman,
    write_json,
)

from dataagent.pipeline.quality.table_metrics import table_page_metrics
from dataagent.pipeline.quality.text_metrics import text_quality_metrics

FEATURES = (
    "glyph_coverage",
    "char_density",
    "clean_spacing",
    "prose_ratio",
    "numbering_ratio",
    "table_cell_fill",
)
SPEARMAN_MIN = 0.8
AGREEMENT_MIN = 0.85
TEXT_AREA_CM2 = 468.0
_TRUE = {"是", "true", "yes", "1", "y"}
_DIMS = ("dim_text", "dim_structure", "dim_semantic", "dim_layout")


def calibrate() -> dict[str, Any]:
    annotations = {
        row["sample_id"]: row
        for row in read_csv(GOLDEN_ROOT / "annotations.csv")
    }
    manifest = {
        row["sample_id"]: row for row in read_csv(GOLDEN_ROOT / "manifest.csv")
    }
    records = [
        record
        for sample_id, row in annotations.items()
        if (record := _record(row, manifest.get(sample_id))) is not None
    ]
    if len(records) < 12:
        report = {
            "status": "insufficient_labels",
            "labeled": len(records),
            "required": 12,
            "note": "请先完成 human_sheet.csv 的人工四维打分并回填 annotations.csv",
        }
        write_json(GOLDEN_ROOT / "calibration_report.json", report)
        return report

    stale = [
        record["sample_id"]
        for record in records
        if record.get("provenance") != "valid"
    ]
    if stale:
        report = {
            "status": "stale_labels",
            "labeled": len(records),
            "valid": len(records) - len(stale),
            "stale": stale,
            "note": (
                "部分标签对应的产物已变化（或缺少 artifact_hash）；"
                "请用最终产物查看器重评这些页后再标定"
            ),
        }
        write_json(GOLDEN_ROOT / "calibration_report.json", report)
        return report

    fit, holdout = _split(records)
    cv = cross_validate(records)
    weights = fit_nonnegative_weights(
        [_vector(record) for record in fit],
        [record["target"] for record in fit],
    )
    weights_full = fit_nonnegative_weights(
        [_vector(record) for record in records],
        [record["target"] for record in records],
    )
    for record in records:
        record["quality"] = _score(record, weights)

    fit_pairs = [(record["quality"], record["needs_manual"]) for record in fit]
    threshold_fit, fit_agreement = best_agreement_threshold(fit_pairs)
    full_scores = [
        (_score(record, weights_full), record["needs_manual"])
        for record in records
    ]
    threshold, full_agreement = best_agreement_threshold(full_scores)
    holdout_pred = [record["quality"] < threshold_fit for record in holdout]
    holdout_truth = [record["needs_manual"] for record in holdout]
    holdout_spearman = spearman(
        [record["quality"] for record in holdout],
        [record["target"] for record in holdout],
    )
    holdout_agreement = _agreement(holdout_pred, holdout_truth)
    holdout_qc = _confusion(holdout_pred, holdout_truth)

    report = {
        "status": "ok",
        "method": "production_l1_components",
        "features_used": list(FEATURES),
        "text_area_cm2": TEXT_AREA_CM2,
        "weights": weights,
        "weights_full": weights_full,
        "threshold": threshold,
        "threshold_fit": threshold_fit,
        "fit_agreement": round(fit_agreement, 4),
        "full_agreement": round(full_agreement, 4),
        "holdout_spearman": round(holdout_spearman, 4),
        "holdout_agreement": round(holdout_agreement, 4),
        "holdout_qc": holdout_qc,
        "counts": {"fit": len(fit), "holdout": len(holdout)},
        "cross_validation": cv,
        "gates": {
            "spearman_min": SPEARMAN_MIN,
            "agreement_min": AGREEMENT_MIN,
            "spearman_pass": cv["spearman_mean"] > SPEARMAN_MIN,
            "agreement_pass": cv["agreement_mean"] >= AGREEMENT_MIN,
        },
    }
    write_json(GOLDEN_ROOT / "calibration_report.json", report)
    return report


def _record(
    row: dict[str, str], manifest: dict[str, str] | None
) -> dict[str, Any] | None:
    if row.get("label_source", "").strip().lower() != "human":
        return None
    dims = [row.get(name, "").strip() for name in _DIMS]
    if not all(value in {"0", "1", "2", "3"} for value in dims):
        return None
    needs_manual = row.get("needs_manual", "").strip().lower()
    if needs_manual not in _TRUE and needs_manual not in {"否", "false", "no", "0", "n"}:
        return None
    markdown = _read_markdown(row["sample_id"])
    meta = manifest or {}
    scanned = str(meta.get("text_layer", "")) == "pure_scan" or (
        str(meta.get("needs_ocr", "")).lower() == "true"
    )
    return {
        "sample_id": row["sample_id"],
        "category": row.get("category", ""),
        "target": sum(int(value) for value in dims) / 12,
        "needs_manual": needs_manual in _TRUE,
        "features": _features(markdown, scanned=scanned),
        "provenance": _provenance(row, markdown),
    }


def _features(markdown: str, *, scanned: bool) -> dict[str, float]:
    chars = [char for char in markdown if not char.isspace()]
    count = len(chars)
    bad = markdown.count("�")
    density_threshold = 0.2 if scanned else 2.0
    text = text_quality_metrics(markdown)
    table = table_page_metrics(markdown) if "|" in markdown else None
    return {
        "glyph_coverage": (1 - bad / count) if count else 0.0,
        "char_density": min((count / TEXT_AREA_CM2) / density_threshold, 1.0),
        "clean_spacing": text.clean_spacing,
        "prose_ratio": text.prose_ratio,
        "numbering_ratio": min(text.numbering_ratio, 1.0),
        "table_cell_fill": table.cell_fill if table is not None else 1.0,
    }


def _read_markdown(sample_id: str) -> str:
    """优先读最终交付产物；旧的原始抽取仅作回退（两批未变化页内容一致）。"""
    for root in (GOLDEN_ROOT / "parsed_final", GOLDEN_ROOT / "parsed"):
        path = root / f"{sample_id}.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def _markdown_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]


def _provenance(row: dict[str, str], markdown: str) -> str:
    """标签是否对应当前展示的产物；旧标签在产物未变化时仍有效。"""
    recorded = row.get("artifact_hash", "").strip()
    if recorded:
        return "valid" if recorded == _markdown_hash(markdown) else "stale"
    raw_path = GOLDEN_ROOT / "parsed" / f"{row['sample_id']}.md"
    if not raw_path.is_file():
        return "stale"
    raw = raw_path.read_text(encoding="utf-8")
    return "valid" if raw.strip() == markdown.strip() else "stale"


def _vector(record: dict[str, Any]) -> dict[str, float]:
    return {name: float(record["features"].get(name, 0.0)) for name in FEATURES}


def _score(record: dict[str, Any], weights: dict[str, float]) -> float:
    return sum(
        weights.get(name, 0.0) * float(record["features"].get(name, 0.0))
        for name in FEATURES
    )


def cross_validate(records: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    """分类别 + needs_manual 分层的 k 折交叉验证。"""
    spearmans: list[float] = []
    agreements: list[float] = []
    precisions: list[float] = []
    recalls: list[float] = []
    for fit, holdout in _folds(records, k):
        if len(fit) < 12:
            continue
        weights = fit_nonnegative_weights(
            [_vector(record) for record in fit],
            [record["target"] for record in fit],
        )
        threshold, _ = best_agreement_threshold(
            [(_score(record, weights), record["needs_manual"]) for record in fit]
        )
        predicted = [_score(record, weights) < threshold for record in holdout]
        truth = [record["needs_manual"] for record in holdout]
        spearmans.append(
            spearman(
                [_score(record, weights) for record in holdout],
                [record["target"] for record in holdout],
            )
        )
        agreements.append(_agreement(predicted, truth))
        confusion = _confusion(predicted, truth)
        precisions.append(confusion["precision"])
        recalls.append(confusion["recall"])
    return {
        "folds": len(spearmans),
        "spearman_mean": round(_mean(spearmans), 4),
        "spearman_std": round(_std(spearmans), 4),
        "agreement_mean": round(_mean(agreements), 4),
        "agreement_std": round(_std(agreements), 4),
        "qc_precision_mean": round(_mean(precisions), 4),
        "qc_recall_mean": round(_mean(recalls), 4),
    }


def _folds(
    records: list[dict[str, Any]], k: int
) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault((record["category"], record["needs_manual"]), []).append(
            record
        )
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(k)]
    for key in sorted(groups):
        rows = sorted(groups[key], key=lambda item: str(item["sample_id"]))
        for index, record in enumerate(rows):
            buckets[index % k].append(record)
    folds: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for bucket in buckets:
        if not bucket:
            continue
        fit = [record for other in buckets if other is not bucket for record in other]
        folds.append((fit, bucket))
    return folds


def _split(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """分类别 + needs_manual 分层切分，保证留出集含正例。"""
    by_category: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_category.setdefault(record["category"], []).append(record)
    fit: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    for category in sorted(by_category):
        rows = sorted(
            by_category[category], key=lambda item: str(item["sample_id"])
        )
        positives = [row for row in rows if row["needs_manual"]]
        negatives = [row for row in rows if not row["needs_manual"]]
        for group in (positives, negatives):
            if not group:
                continue
            fit_count = max(1, round(len(group) * 2 / 3))
            fit.extend(group[:fit_count])
            holdout.extend(group[fit_count:])
    return fit, holdout


def _agreement(predicted: list[bool], truth: list[bool]) -> float:
    if not truth:
        return 0.0
    return sum(1 for p, t in zip(predicted, truth, strict=True) if p == t) / len(truth)


def _confusion(predicted: list[bool], truth: list[bool]) -> dict[str, float]:
    tp = sum(1 for p, t in zip(predicted, truth, strict=True) if p and t)
    fp = sum(1 for p, t in zip(predicted, truth, strict=True) if p and not t)
    fn = sum(1 for p, t in zip(predicted, truth, strict=True) if not p and t)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else 0.0,
        "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
    }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5


if __name__ == "__main__":
    print(json.dumps(calibrate(), ensure_ascii=False, indent=2))
