"""extract_quality 标定（B9/T123 D 步）。

输入：`var/golden/annotations.csv`（人工子集）+ `var/golden/metrics.csv`（代理指标）。
流程：按类别分层 2/3 拟合、1/3 留出；无截距最小二乘拟合权重（负权截断 + 归一化）；
在留出集报告 Spearman 与 needs_manual 一致率；门禁 Spearman > 0.8、一致率 ≥ 0.85。

没有人工标注时输出 `insufficient_labels` 并以 0 退出（不阻塞 CI）。
"""

from __future__ import annotations

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

# 7 个有明确依据的特征：文本量 / 空白伪影 / 表格质量 / 表格占比 /
# 编号保留 / 段落回流 / 页眉页脚泄漏。n=59 时限制维数避免过拟合。
FEATURES = (
    "char_density",
    "clean_spacing",
    "table_cell_fill",
    "prose_ratio",
    "numbering_ratio",
    "paragraph_flow",
    "header_footer_ratio",
)
SPEARMAN_MIN = 0.8
AGREEMENT_MIN = 0.85


def calibrate() -> dict[str, Any]:
    annotations = read_csv(GOLDEN_ROOT / "annotations.csv")
    metrics = {row["sample_id"]: row for row in read_csv(GOLDEN_ROOT / "metrics.csv")}
    records = [
        record
        for record in (_record(row, metrics) for row in annotations)
        if record is not None
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

    fit, holdout = _split(records)
    active = _active_features(fit)
    density_scale = max(
        (record["features"]["char_density"] for record in fit), default=1.0
    ) or 1.0
    for record in records:
        record["features"]["char_density"] /= density_scale

    weights = fit_nonnegative_weights(
        [
            {name: record["features"].get(name, 0.0) for name in active}
            for record in fit
        ],
        [record["target"] for record in fit],
    )
    for record in records:
        record["quality"] = sum(
            weights.get(name, 0.0) * record["features"].get(name, 0.0)
            for name in active
        )

    fit_pairs = [(record["quality"], record["needs_manual"]) for record in fit]
    threshold, fit_agreement = best_agreement_threshold(fit_pairs)
    holdout_spearman = spearman(
        [record["quality"] for record in holdout],
        [record["target"] for record in holdout],
    )
    holdout_agreement = (
        sum(
            1
            for record in holdout
            if (record["quality"] < threshold) == record["needs_manual"]
        )
        / len(holdout)
        if holdout
        else 0.0
    )

    report = {
        "status": "ok",
        "weights": weights,
        "features_used": active,
        "density_scale": density_scale,
        "threshold": threshold,
        "fit_agreement": round(fit_agreement, 4),
        "holdout_spearman": round(holdout_spearman, 4),
        "holdout_agreement": round(holdout_agreement, 4),
        "counts": {"fit": len(fit), "holdout": len(holdout)},
        "gates": {
            "spearman_min": SPEARMAN_MIN,
            "agreement_min": AGREEMENT_MIN,
            "spearman_pass": holdout_spearman > SPEARMAN_MIN,
            "agreement_pass": holdout_agreement >= AGREEMENT_MIN,
        },
    }
    write_json(GOLDEN_ROOT / "calibration_report.json", report)
    return report


def _record(
    row: dict[str, str], metrics: dict[str, dict[str, str]]
) -> dict[str, Any] | None:
    if row.get("label_source", "").strip().lower() != "human":
        return None
    dims = [
        row.get("dim_text", ""),
        row.get("dim_structure", ""),
        row.get("dim_semantic", ""),
        row.get("dim_layout", ""),
    ]
    if not all(value.strip() for value in dims):
        return None
    needs_manual = row.get("needs_manual", "").strip().lower()
    if needs_manual not in {"是", "否", "true", "false", "yes", "no", "1", "0"}:
        return None
    metric = metrics.get(row["sample_id"])
    if metric is None:
        return None
    total = sum(int(value) for value in dims) / 12
    # 表格指标：非表格页取中性值（fill=1），不干扰拟合
    features = {name: float(metric.get(name, 0) or 0) for name in FEATURES}
    features["table_cell_fill"] = float(metric.get("table_cell_fill") or 1.0)
    return {
        "sample_id": row["sample_id"],
        "category": row["category"],
        "target": total,
        "needs_manual": needs_manual in {"是", "true", "yes", "1"},
        "features": features,
    }


def _active_features(fit: list[dict[str, Any]]) -> list[str]:
    """丢弃拟合集上身零方差的特征（常量列只会干扰回归）。"""
    active: list[str] = []
    for name in FEATURES:
        values = [record["features"].get(name, 0.0) for record in fit]
        mean = sum(values) / len(values) if values else 0.0
        variance = (
            sum((value - mean) ** 2 for value in values) / len(values)
            if values
            else 0.0
        )
        if variance > 1e-12:
            active.append(name)
    return active


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


if __name__ == "__main__":
    print(json.dumps(calibrate(), ensure_ascii=False, indent=2))
