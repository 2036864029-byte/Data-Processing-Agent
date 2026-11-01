"""B9 工具链单元测试：指标、抽样、拟合。"""

import pytest
from _lib import (
    PageSignals,
    balanced_quotas,
    best_agreement_threshold,
    character_error_rate,
    classify_page,
    deterministic_sample,
    field_exact_match,
    fit_nonnegative_weights,
    mark_cross_page_tables,
    parse_markdown_tables,
    plain_text,
    reading_order_accuracy,
    spearman,
    table_cell_f1,
)


def test_cer_identical_and_substitution() -> None:
    assert character_error_rate("hello world", "hello world") == 0.0
    assert character_error_rate("hello", "hallo") == pytest.approx(0.2)


def test_reading_order_is_order_sensitive() -> None:
    reference = ["alpha", "bravo", "charlie"]

    assert reading_order_accuracy(reference, "alpha\nbravo\ncharlie") == 1.0
    assert reading_order_accuracy(reference, "charlie\nbravo\nalpha") < 0.5


def test_table_cell_f1() -> None:
    grid = [["a", "b"], ["1", "2"]]

    assert table_cell_f1(grid, grid) == 1.0
    assert table_cell_f1(grid, [["x", "y"], ["9", "8"]]) == 0.0
    assert 0.0 < table_cell_f1(grid, [["a", "b"], ["1", "9"]]) < 1.0


def test_field_exact_match_with_numeric_tolerance() -> None:
    reference = {"amount": 1234.5, "name": "Acme"}

    assert field_exact_match(reference, {"amount": 1234.5001, "name": "Acme"}) == 1.0
    assert field_exact_match(reference, {"amount": 1.0, "name": "Acme"}) == 0.5
    assert field_exact_match(reference, {}) == 0.0


def test_spearman_perfect_monotonic() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_weight_fit_is_normalized_and_non_negative() -> None:
    features = [
        {"a": 1.0, "b": 0.0},
        {"a": 0.8, "b": 0.2},
        {"a": 0.2, "b": 0.8},
        {"a": 0.0, "b": 1.0},
    ]
    target = [1.0, 0.8, 0.2, 0.0]

    weights = fit_nonnegative_weights(features, target)

    assert sum(weights.values()) == pytest.approx(1.0)
    assert all(value >= 0 for value in weights.values())
    assert weights["a"] > weights["b"]


def test_balanced_quotas_sum_and_balance() -> None:
    quotas = balanced_quotas(200)

    assert sum(quotas.values()) == 200
    assert max(quotas.values()) - min(quotas.values()) <= 1


def test_deterministic_sample_is_reproducible() -> None:
    items = list(range(100))

    first = deterministic_sample(items, 10)
    second = deterministic_sample(items, 10)

    assert first == second
    assert first == sorted(first)
    assert len(first) == 10


def test_cross_page_table_classification() -> None:
    pages = {
        1: PageSignals(has_table=True),
        2: PageSignals(has_table=True),
        3: PageSignals(has_column=True),
    }

    marked = mark_cross_page_tables(pages)

    assert classify_page(marked[1]) == "cross_page_table"
    assert marked[2].has_table is False
    assert classify_page(marked[3]) == "multi_column"


def test_classification_priority() -> None:
    assert (
        classify_page(PageSignals(has_table=True, encoding_bad=True))
        == "encoding_broken"
    )
    assert classify_page(PageSignals(has_table=True, needs_ocr=True)) == "scanned"
    assert classify_page(PageSignals(has_column=True)) == "multi_column"
    assert classify_page(PageSignals()) == "text_only"


def test_markdown_table_parsing_and_plain_text() -> None:
    markdown = "# Title\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\nbody line\n"

    tables = parse_markdown_tables(markdown)
    text = plain_text(markdown)

    assert tables == [[["a", "b"], ["1", "2"]]]
    assert "Title" in text
    assert "body line" in text
    assert "|" not in text


def test_best_agreement_threshold() -> None:
    pairs = [(0.2, True), (0.9, False), (0.4, True), (0.95, False)]

    threshold, agreement = best_agreement_threshold(pairs)

    assert agreement == 1.0
    assert 0.4 < threshold <= 0.9
