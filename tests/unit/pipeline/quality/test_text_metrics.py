"""产物文本结构指标（B9/T123）：生产 L1 与黄金集标定共用同一口径。"""

from __future__ import annotations

from dataagent.pipeline.quality.text_metrics import (
    TextQualityMetrics,
    text_quality_metrics,
)

PROSE = (
    "第一段文字完整，句末有标点。\n"
    "第二段文字也完整，句末有标点。\n"
    "第三段文字结束。\n"
)


def test_empty_markdown_is_all_zero() -> None:
    metrics = text_quality_metrics("")

    assert metrics == TextQualityMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_clean_prose_scores_high() -> None:
    metrics = text_quality_metrics(PROSE)

    assert metrics.paragraph_flow == 1.0
    assert metrics.prose_ratio == 1.0
    assert metrics.clean_spacing == 1.0
    assert metrics.numbering_ratio == 0.0
    assert metrics.heading_ratio == 0.0


def test_cjk_spaces_reduce_clean_spacing() -> None:
    spaced = "中 文 字 符 之 间 有 空 格。\n"
    metrics = text_quality_metrics(spaced)

    assert metrics.clean_spacing < 1.0


def test_hard_wraps_reduce_paragraph_flow() -> None:
    wrapped = "这一行没有终止标点\n这一行也没有\n"
    metrics = text_quality_metrics(wrapped)

    assert metrics.paragraph_flow == 0.0


def test_numbering_lines_are_counted() -> None:
    numbered = "第一条 内容完整。\n一、 内容完整。\n（2）内容完整。\n普通段落。\n"
    metrics = text_quality_metrics(numbered)

    assert 0.0 < metrics.numbering_ratio <= 1.0


def test_heading_markers_are_counted() -> None:
    with_heading = "# 标题\n\n正文内容完整。\n"
    metrics = text_quality_metrics(with_heading)

    assert metrics.heading_ratio > 0.0


def test_table_lines_reduce_prose_ratio() -> None:
    table_only = "|a|b|\n|---|---|\n|1|2|\n"
    metrics = text_quality_metrics(table_only)

    assert metrics.prose_ratio == 0.0


def test_as_dict_exposes_rounded_fields() -> None:
    payload = text_quality_metrics(PROSE).as_dict()

    assert set(payload) == {
        "paragraph_flow",
        "line_length_variety",
        "clean_spacing",
        "numbering_ratio",
        "heading_ratio",
        "prose_ratio",
    }
    assert all(value == round(value, 4) for value in payload.values())
