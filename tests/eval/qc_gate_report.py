"""QC 门禁评测（B9/T124）：在 60 页人工子集上对当前流水线取逐页 verdict。

与 `calibrate.py` 的区别：标定用代理指标拟合分数；本脚本跑**当前流水线**，
用真实 `PageReport.verdict / extract_quality` 对人工 `needs_manual` 计算
QC precision / recall，并报告系统质量分与人工总分的 Spearman。

输出 `var/golden/qc_gate_report.json`（本地私有，不入库）。

用法：
    python tests/eval/qc_gate_report.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from _lib import GOLDEN_ROOT, read_csv, read_json, spearman, write_json

from dataagent.contracts.messages import Verdict
from dataagent.pipeline.quality.gate import InMemoryQualityResultStore
from dataagent.pipeline.task.factory import create_builtin_runner

_TRUE = {"是", "true", "yes", "1", "y"}
_DIMS = ("dim_text", "dim_structure", "dim_semantic", "dim_layout")
QC_PRECISION_MIN = 0.95
QC_RECALL_MIN = 0.90


def collect() -> dict[str, Any]:
    sheet = read_csv(GOLDEN_ROOT / "human_sheet.csv")
    manifest = {
        row["sample_id"]: row for row in read_json(GOLDEN_ROOT / "manifest.json")
    }
    annotations = {
        row["sample_id"]: row for row in read_csv(GOLDEN_ROOT / "annotations.csv")
    }

    by_file: dict[str, set[int]] = defaultdict(set)
    for row in sheet:
        meta = manifest[row["sample_id"]]
        by_file[meta["file"]].add(int(meta["page_no"]))

    store = InMemoryQualityResultStore()
    failures: dict[str, str] = {}
    doc_ids: dict[tuple[str, int], str] = {}
    with TemporaryDirectory() as output_dir:
        runner = create_builtin_runner(output_dir=output_dir, quality_store=store)
        for file_name, pages in sorted(by_file.items()):
            try:
                outcome = runner.process_pages(
                    Path(file_name), sorted(pages), task_id="qc-gate"
                )
                for page in pages:
                    doc_ids[(file_name, page)] = outcome.doc_id
            except Exception as exc:  # 单文件失败不影响其余样本
                failures[file_name] = f"{type(exc).__name__}: {exc}"

    samples: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in sheet:
        sample_id = row["sample_id"]
        meta = manifest[sample_id]
        page_no = int(meta["page_no"])
        # 构造样本的 manifest 未写 doc_id；用本次运行的实际 doc_id 兜底
        doc_id = meta.get("doc_id") or doc_ids.get((meta["file"], page_no))
        report = store.load(doc_id, page_no) if doc_id else None
        if report is None:
            missing.append(sample_id)
            continue
        annotation = annotations.get(sample_id)
        if annotation is None:
            continue
        total = sum(int(annotation[dim]) for dim in _DIMS) / 12
        samples.append(
            {
                "sample_id": sample_id,
                "category": meta["category"],
                "needs_manual": annotation["needs_manual"].strip().lower()
                in _TRUE,
                "verdict": report.verdict.value,
                "extract_quality": report.extract_quality,
                "human_total": total,
                "defects": [defect.code.value for defect in report.defects],
            }
        )

    if not samples:
        return {
            "status": "no_samples",
            "failures": failures,
            "missing": missing,
        }

    threshold = _calibrated_threshold()
    gates = {
        "qc_precision_min": QC_PRECISION_MIN,
        "qc_recall_min": QC_RECALL_MIN,
    }
    verdict_confusion = _confusion(
        [s["verdict"] == Verdict.FAIL.value for s in samples],
        [s["needs_manual"] for s in samples],
    )
    gates.update(
        {
            "qc_precision": verdict_confusion["precision"],
            "qc_recall": verdict_confusion["recall"],
            "qc_precision_pass": verdict_confusion["precision"] >= QC_PRECISION_MIN,
            "qc_recall_pass": verdict_confusion["recall"] >= QC_RECALL_MIN,
        }
    )

    threshold_confusion: dict[str, float] | None = None
    if threshold is not None:
        threshold_confusion = _confusion(
            [s["extract_quality"] < threshold for s in samples],
            [s["needs_manual"] for s in samples],
        )

    return {
        "status": "ok",
        "samples": len(samples),
        "candidates": len(sheet),
        "missing": missing,
        "failures": failures,
        "threshold": threshold,
        "verdict_confusion": verdict_confusion,
        "threshold_confusion": threshold_confusion,
        "spearman_quality_vs_total": round(
            spearman(
                [s["extract_quality"] for s in samples],
                [s["human_total"] for s in samples],
            ),
            4,
        ),
        "page_pass_rate": round(
            sum(1 for s in samples if s["verdict"] != Verdict.FAIL.value)
            / len(samples),
            4,
        ),
        "by_category": _by_category(samples),
        "gates": gates,
        "details": samples,
    }


def _calibrated_threshold() -> float | None:
    path = GOLDEN_ROOT / "calibration_report.json"
    if not path.is_file():
        return None
    value = read_json(path).get("threshold")
    return float(value) if isinstance(value, (int, float)) else None


def _confusion(predicted: list[bool], truth: list[bool]) -> dict[str, float]:
    tp = sum(1 for p, t in zip(predicted, truth, strict=True) if p and t)
    fp = sum(1 for p, t in zip(predicted, truth, strict=True) if p and not t)
    fn = sum(1 for p, t in zip(predicted, truth, strict=True) if not p and t)
    tn = sum(1 for p, t in zip(predicted, truth, strict=True) if not p and not t)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "accuracy": round((tp + tn) / len(truth), 4) if truth else 0.0,
    }


def _by_category(samples: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        grouped[sample["category"]].append(sample)
    result: dict[str, dict[str, float]] = {}
    for category, rows in sorted(grouped.items()):
        confusion = _confusion(
            [row["verdict"] == Verdict.FAIL.value for row in rows],
            [row["needs_manual"] for row in rows],
        )
        result[category] = {
            "samples": len(rows),
            "positives": sum(1 for row in rows if row["needs_manual"]),
            "page_pass_rate": round(
                sum(1 for row in rows if row["verdict"] != Verdict.FAIL.value)
                / len(rows),
                4,
            ),
            **confusion,
        }
    return result


if __name__ == "__main__":
    report = collect()
    write_json(GOLDEN_ROOT / "qc_gate_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report.get("status") == "ok" else 1)
