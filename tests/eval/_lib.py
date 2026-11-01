"""B9 黄金集工具链公共函数（A/B/D）。

- 六类分层与确定性抽样；
- 自动指标：CER / 阅读顺序 LCS 一致率 / 表格 cell F1 / 字段 exact match；
- Spearman 相关与权重拟合（纯 Python，无 numpy）；
- CSV/JSON 读写。

样本与标注属本地私有：所有输出写到 `var/golden/`（已被 .gitignore 排除）。
"""

from __future__ import annotations

import csv
import json
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

CATEGORIES: Final[tuple[str, ...]] = (
    "text_only",
    "single_table",
    "cross_page_table",
    "multi_column",
    "scanned",
    "encoding_broken",
)
CONSTRUCTIBLE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"text_only", "single_table", "cross_page_table", "multi_column"}
)
GOLDEN_ROOT: Final[Path] = Path("var/golden")
DEFAULT_SEED: Final[int] = 20261101
HUMAN_PAGES_PER_CATEGORY: Final[int] = 10


@dataclass(frozen=True, slots=True)
class PageSignals:
    has_table: bool = False
    has_column: bool = False
    needs_ocr: bool = False
    encoding_bad: bool = False
    cross_page_table: bool = False
    ocr_reason: str | None = None


def classify_page(signals: PageSignals) -> str:
    """类别互斥，按最强信号优先：编码损坏 > 扫描 > 跨页表 > 单表 > 多栏 > 纯文本。"""
    if signals.encoding_bad:
        return "encoding_broken"
    if signals.needs_ocr:
        return "scanned"
    if signals.cross_page_table:
        return "cross_page_table"
    if signals.has_table:
        return "single_table"
    if signals.has_column:
        return "multi_column"
    return "text_only"


def mark_cross_page_tables(pages: Mapping[int, PageSignals]) -> dict[int, PageSignals]:
    """相邻两页都有表格 → 起始页标记为跨页表，后续页不再重复计入。"""
    marked: dict[int, PageSignals] = {}
    for page_no in sorted(pages):
        signals = pages[page_no]
        continuation = (
            page_no - 1 in pages
            and pages[page_no - 1].has_table
            and not pages[page_no - 1].encoding_bad
        )
        cross = signals.has_table and page_no + 1 in pages and pages[page_no + 1].has_table
        marked[page_no] = PageSignals(
            has_table=signals.has_table and not continuation,
            has_column=signals.has_column,
            needs_ocr=signals.needs_ocr,
            encoding_bad=signals.encoding_bad,
            cross_page_table=cross and not continuation,
            ocr_reason=signals.ocr_reason,
        )
    return marked


def balanced_quotas(total: int, categories: Sequence[str] = CATEGORIES) -> dict[str, int]:
    base, remainder = divmod(total, len(categories))
    return {
        category: base + (1 if index < remainder else 0)
        for index, category in enumerate(categories)
    }


def deterministic_sample(
    items: Sequence[Any],
    count: int,
    *,
    seed: int = DEFAULT_SEED,
    key: Any = None,
) -> list[Any]:
    """排序后按固定种子抽样，跨机器可复现。"""
    ordered = sorted(items, key=key) if key is not None else sorted(items)
    if count >= len(ordered):
        return list(ordered)
    rng = random.Random(seed)
    sampled = rng.sample(ordered, count)
    return sorted(sampled, key=key) if key is not None else sorted(sampled)


def character_error_rate(reference: str, hypothesis: str) -> float:
    reference, hypothesis = _normalize_text(reference), _normalize_text(hypothesis)
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return levenshtein(reference, hypothesis) / len(reference)


def levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def reading_order_accuracy(
    reference_lines: Sequence[str], hypothesis: str
) -> float:
    """词级 LCS 覆盖率（顺序一致率）。

    按词而非按行比较：Markdown 会把同一栏的连续行合并成段落，按行比较会
    把"正常的段落合并"误判成顺序错误；分栏错序会改变词序，词级 LCS 能捕捉。
    """
    reference = _words(" ".join(reference_lines))
    if not reference:
        return 1.0
    matched = _lcs_length(reference, _words(hypothesis))
    return matched / len(reference)


def field_exact_match(
    reference: Mapping[str, Any], extracted: Mapping[str, Any]
) -> float:
    if not reference:
        return 1.0
    hits = sum(
        1 for key, value in reference.items() if _same_value(value, extracted.get(key))
    )
    return hits / len(reference)


def table_cell_f1(
    reference: Sequence[Sequence[str]], hypothesis: Sequence[Sequence[str]]
) -> float:
    """cell 级 F1（TEDS 的确定性代理指标；P0 不用树编辑距离）。"""
    reference_cells = _cell_multiset(reference)
    hypothesis_cells = _cell_multiset(hypothesis)
    if not reference_cells and not hypothesis_cells:
        return 1.0
    if not reference_cells or not hypothesis_cells:
        return 0.0
    overlap = sum(
        min(count, hypothesis_cells.get(cell, 0))
        for cell, count in reference_cells.items()
    )
    precision = overlap / sum(hypothesis_cells.values())
    recall = overlap / sum(reference_cells.values())
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def spearman(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    return _pearson(_ranks(left), _ranks(right))


def fit_nonnegative_weights(
    features: Sequence[Mapping[str, float]],
    target: Sequence[float],
) -> dict[str, float]:
    """无截距最小二乘 + 负权截断 + 归一化（权重必须由黄金集拟合，不得拍脑袋）。"""
    if not features:
        return {}
    names = sorted({name for row in features for name in row})
    if not names:
        return {}
    matrix = [[row.get(name, 0.0) for name in names] for row in features]
    size = len(names)
    xtx = [[0.0] * size for _ in range(size)]
    xty = [0.0] * size
    for row, value in zip(matrix, target, strict=True):
        for i in range(size):
            xty[i] += row[i] * value
            for j in range(size):
                xtx[i][j] += row[i] * row[j]
    for i in range(size):
        xtx[i][i] += 1e-6  # ridge，避免奇异
    weights = _solve(xtx, xty)
    clamped = [max(0.0, weight) for weight in weights]
    total = sum(clamped)
    if total == 0:
        clamped, total = [1.0] * size, float(size)
    return {name: clamped[index] / total for index, name in enumerate(names)}


def needs_manual_agreement(
    pairs: Sequence[tuple[float, bool]], threshold: float
) -> float:
    if not pairs:
        return 0.0
    hits = sum(1 for quality, label in pairs if (quality < threshold) == label)
    return hits / len(pairs)


def best_agreement_threshold(
    pairs: Sequence[tuple[float, bool]],
) -> tuple[float, float]:
    """扫描阈值，取一致率最大者；并列时取更接近 0.85 的阈值。"""
    best = (0.5, -1.0)
    for step in range(30, 100):
        threshold = step / 100
        score = needs_manual_agreement(pairs, threshold)
        if score > best[1] + 1e-12 or (
            abs(score - best[1]) <= 1e-12
            and abs(threshold - 0.85) < abs(best[0] - 0.85)
        ):
            best = (threshold, score)
    return best


def parse_markdown_tables(markdown: str) -> list[list[list[str]]]:
    """把 Markdown 中的表格解析为 grid 列表（供 cell F1 使用）。"""
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if current:
                blocks.append(current)
                current = []
            continue
        cells = _split_cells(stripped)
        if all(cell and set(cell) <= {"-", ":"} for cell in cells):
            continue
        current.append(cells)
    if current:
        blocks.append(current)
    return blocks


def plain_text(markdown: str) -> str:
    """去掉 Markdown 标记，便于 CER/阅读顺序对比。"""
    lines = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("|"):
            continue
        lines.append(stripped.lstrip("#").strip())
    return "\n".join(lines)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _words(text: str) -> list[str]:
    return _normalize_text(text).split() if text.strip() else []


def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    for left_item in left:
        current = [0]
        for index, right_item in enumerate(right, start=1):
            if left_item == right_item:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[index - 1]))
        previous = current
    return previous[-1]


def _cell_multiset(grid: Sequence[Sequence[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in grid:
        for cell in row:
            value = _normalize_text(cell)
            if value:
                counts[value] = counts.get(value, 0) + 1
    return counts


def _split_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _same_value(reference: Any, hypothesis: Any) -> bool:
    if reference is None or hypothesis is None:
        return reference is hypothesis
    try:
        return math.isclose(float(reference), float(hypothesis), rel_tol=1e-6)
    except (TypeError, ValueError):
        return _normalize_text(str(reference)) == _normalize_text(str(hypothesis))


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average = (index + end) / 2 + 1
        for position in range(index, end + 1):
            ranks[order[position]] = average
        index = end + 1
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True)
    )
    left_var = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_var = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_var == 0 or right_var == 0:
        return 0.0
    return numerator / (left_var * right_var)


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        if abs(augmented[column][column]) < 1e-12:
            continue
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column] / augmented[column][column]
            for position in range(column, size + 1):
                augmented[row][position] -= factor * augmented[column][position]
    return [
        augmented[index][size] / augmented[index][index]
        if abs(augmented[index][index]) > 1e-12
        else 0.0
        for index in range(size)
    ]
