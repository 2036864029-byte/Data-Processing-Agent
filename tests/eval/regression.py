"""质量回归基线与前后对比（60 页人工子集）。

用途：解析/后处理优化后重跑 `prepare_review.py`，再运行本脚本对比自动指标，
不依赖人工逐页重打分即可发现回退；里程碑再用人工抽检验证。

指标（全部来自产物 Markdown / 表格解析）：
- phantom_cjk_space_rate：汉字间伪空格（每千字）
- hard_wrap_ratio：段落硬换行比例（行尾无终止标点）
- numbering_per_1000：编号/条款行密度（每千字）
- malformed_numbering_per_1000：错序/非规范编号密度（每千字）
- prose_ratio：非表格行占比
- table_cell_fill / table_header_ok / table_column_variance：表格指标
- reference_cer：与 PDFium 文本层的字符错误率（忽略空白，仅 present 页）

用法：
    python tests/eval/regression.py --update-baseline
    python tests/eval/regression.py        # 与基线对比，回退则退出码 1
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _lib import (
    GOLDEN_ROOT,
    character_error_rate,
    read_csv,
    read_json,
    write_json,
)

from dataagent.pipeline.quality.numbering import malformed_numbering_count
from dataagent.pipeline.quality.table_metrics import table_page_metrics

REGRESSION_BASELINE = GOLDEN_ROOT / "regression_baseline.json"
DEFAULT_BASELINE = REGRESSION_BASELINE
DEFAULT_TOLERANCE = 0.02

_TERMINAL_PUNCTUATION = "。！？；：.!?;:）)】」』,，"
_NUMBERING = re.compile(
    r"^(第[一二三四五六七八九十百千0-9]+[条章节款]"
    r"|[一二三四五六七八九十]+、"
    r"|[（(]\d+[）)]"
    r"|[①-⑳]"
    r"|\d+[.、])"
)
_CJK_SPACE = re.compile(r"[一-鿿]\s+[一-鿿]")

LOWER_IS_BETTER = frozenset(
    {
        "phantom_cjk_space_rate",
        "hard_wrap_ratio",
        "table_column_variance",
        "reference_cer",
    }
)
HIGHER_IS_BETTER = frozenset(
    {
        "malformed_numbering_per_1000",
        "numbering_per_1000",
        "prose_ratio",
        "table_cell_fill",
        "table_header_ok",
    }
)


@dataclass(frozen=True, slots=True)
class Comparison:
    metric: str
    baseline: float
    current: float
    delta: float
    status: str  # improved / regressed / unchanged


def markdown_metrics(markdown: str) -> dict[str, float]:
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not lines:
        return {
            "phantom_cjk_space_rate": 0.0,
            "hard_wrap_ratio": 0.0,
            "numbering_per_1000": 0.0,
            "malformed_numbering_per_1000": 0.0,
            "prose_ratio": 0.0,
            "table_cell_fill": 1.0,
            "table_header_ok": 1.0,
            "table_column_variance": 0.0,
        }
    prose = [
        line
        for line in lines
        if not line.startswith("|") and not line.startswith("#")
    ]
    hard_wrap = (
        sum(1 for line in prose if line[-1] not in _TERMINAL_PUNCTUATION) / len(prose)
        if prose
        else 1.0
    )
    phantom = len(_CJK_SPACE.findall(markdown)) / max(1, len(markdown)) * 1000
    numbering = sum(1 for line in prose if _NUMBERING.match(line))
    table_lines = sum(1 for line in lines if line.startswith("|"))
    table = table_page_metrics(markdown)
    return {
        "phantom_cjk_space_rate": round(phantom, 4),
        "hard_wrap_ratio": round(hard_wrap, 4),
        "numbering_per_1000": round(numbering / max(1, len(markdown)) * 1000, 4),
        "malformed_numbering_per_1000": round(
            malformed_numbering_count(markdown) / max(1, len(markdown)) * 1000, 4
        ),
        "prose_ratio": round(1.0 - table_lines / len(lines), 4),
        "table_cell_fill": round(table.cell_fill, 4) if table else 1.0,
        "table_header_ok": 1.0 if (table is None or table.header_ok) else 0.0,
        "table_column_variance": (
            round(table.column_variance, 4) if table else 0.0
        ),
    }


def compute_metrics(
    *,
    golden_root: Path = GOLDEN_ROOT,
    reference_text: Callable[[Path, int], str | None] | None = None,
) -> dict[str, Any]:
    """计算当前 parsed 产物指标（按 sample），并给出分类/总体均值。"""
    sheet = read_csv(golden_root / "human_sheet.csv")
    manifest = {
        row["sample_id"]: row for row in read_json(golden_root / "manifest.json")
    }
    samples: dict[str, dict[str, float]] = {}
    for row in sheet:
        sample_id = row["sample_id"]
        parsed_path = golden_root / "parsed" / f"{sample_id}.md"
        if not parsed_path.exists():
            continue
        markdown = parsed_path.read_text(encoding="utf-8")
        metrics = markdown_metrics(markdown)
        meta = manifest.get(sample_id, {})
        if (
            reference_text is not None
            and str(meta.get("text_layer")) == "present"
        ):
            reference = reference_text(Path(row["file"]), int(row["page_no"]))
            if reference:
                metrics["reference_cer"] = round(
                    _cer_no_space(reference, markdown), 4
                )
        samples[sample_id] = metrics

    by_category: dict[str, list[dict[str, float]]] = defaultdict(list)
    for row in sheet:
        sample_id = row["sample_id"]
        if sample_id in samples:
            by_category[row["category"]].append(samples[sample_id])
    return {
        "samples": samples,
        "overall": _aggregate(list(samples.values())),
        "by_category": {
            category: _aggregate(rows)
            for category, rows in sorted(by_category.items())
        },
    }


def compare(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> list[Comparison]:
    comparisons: list[Comparison] = []
    baseline_overall = baseline.get("overall", {})
    current_overall = current.get("overall", {})
    for metric in sorted(set(baseline_overall) | set(current_overall)):
        old = float(baseline_overall.get(metric, 0.0))
        new = float(current_overall.get(metric, 0.0))
        delta = new - old
        status = "unchanged"
        if metric in LOWER_IS_BETTER:
            if new > old + tolerance:
                status = "regressed"
            elif new < old - tolerance:
                status = "improved"
        elif metric in HIGHER_IS_BETTER:
            if new < old - tolerance:
                status = "regressed"
            elif new > old + tolerance:
                status = "improved"
        comparisons.append(
            Comparison(metric, old, new, round(delta, 4), status)
        )
    return comparisons


def _cer_no_space(reference: str, hypothesis: str) -> float:
    return character_error_rate("".join(reference.split()), "".join(hypothesis.split()))


def _aggregate(rows: Sequence[Mapping[str, float]]) -> dict[str, float]:
    if not rows:
        return {}
    keys = sorted({key for row in rows for key in row})
    return {
        key: round(sum(float(row.get(key, 0.0)) for row in rows) / len(rows), 4)
        for key in keys
    }


def reference_from_pdfium(path: Path, page_no: int) -> str | None:
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(path))
        try:
            return document[page_no - 1].get_textpage().get_text_range()
        finally:
            document.close()
    except Exception:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="60 页产物自动回归对比")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_ROOT)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)

    current = compute_metrics(
        golden_root=args.golden_dir, reference_text=reference_from_pdfium
    )
    if args.update_baseline:
        payload = {
            "version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            **current,
        }
        write_json(args.baseline, payload)
        print(f"baseline 已更新：{args.baseline}")
        return 0

    if not args.baseline.exists():
        print(f"基线不存在：{args.baseline}（先运行 --update-baseline）")
        return 2
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    comparisons = compare(baseline, current, tolerance=args.tolerance)
    regressions = [item for item in comparisons if item.status == "regressed"]
    print(f"{'指标':32s}{'基线':>10s}{'当前':>10s}{'变化':>10s}  状态")
    for item in comparisons:
        print(
            f"{item.metric:32s}{item.baseline:10.4f}{item.current:10.4f}"
            f"{item.delta:+10.4f}  {item.status}"
        )
    print(
        f"\n{len(comparisons) - len(regressions)}/{len(comparisons)} 指标无回退"
    )
    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
