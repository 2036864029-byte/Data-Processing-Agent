"""合并查看器导出的打分到 annotations.csv（B9 标注回流）。

用法：把导出的 `annotations_filled.csv` 放到 `var/golden/`，运行：
    python tests/eval/import_annotations.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from _lib import GOLDEN_ROOT, read_csv, write_csv
from annotations_template import ANNOTATION_FIELDS

_TRUE = {"是", "true", "yes", "1", "y"}
_FALSE = {"否", "false", "no", "0", "n"}
_DIMS = ("dim_text", "dim_structure", "dim_semantic", "dim_layout")


def merge(
    filled_path: Path | None = None, annotations_path: Path | None = None
) -> dict[str, int]:
    filled_path = filled_path or GOLDEN_ROOT / "annotations_filled.csv"
    annotations_path = annotations_path or GOLDEN_ROOT / "annotations.csv"
    filled = read_csv(filled_path)
    annotations = read_csv(annotations_path)
    by_id = {row["sample_id"]: row for row in annotations}

    merged = 0
    skipped = 0
    for row in filled:
        sample_id = row.get("sample_id", "").strip()
        target = by_id.get(sample_id)
        dims = [row.get(name, "").strip() for name in _DIMS]
        if target is None or not all(value in {"0", "1", "2", "3"} for value in dims):
            skipped += 1
            continue
        (
            target["dim_text"],
            target["dim_structure"],
            target["dim_semantic"],
            target["dim_layout"],
        ) = dims
        target["total"] = str(sum(int(value) for value in dims))
        target["needs_manual"] = _normalize_bool(row.get("needs_manual", ""))
        target["error_notes"] = row.get("error_notes", "")
        target["annotator"] = row.get("annotator", "") or target.get("annotator", "")
        target["date"] = row.get("date", "") or date.today().isoformat()
        target["label_source"] = "human"
        merged += 1

    write_csv(annotations_path, annotations, ANNOTATION_FIELDS)
    return {"merged": merged, "skipped": skipped, "total": len(annotations)}


def _normalize_bool(value: str) -> str:
    text = value.strip().lower()
    if text in _TRUE:
        return "是"
    if text in _FALSE or not text:
        return "否"
    raise ValueError(f"needs_manual 取值不合法：{value!r}")


if __name__ == "__main__":
    try:
        print(json.dumps(merge(), ensure_ascii=False))
    except FileNotFoundError as exc:
        print(f"缺少输入文件：{exc}", file=sys.stderr)
        sys.exit(1)
