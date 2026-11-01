"""恢复文本的 L1 指标输入测试（T132，Phase 12 R2）。"""

from dataagent.pipeline.quality.l1_check import L1Metrics


def test_metrics_from_recovered_text() -> None:
    metrics = L1Metrics.from_text("甲乙丙丁�", text_area=100.0)

    assert metrics.char_density == 0.05
    assert metrics.glyph_coverage == 0.8
    assert metrics.cid_unmapped_ratio is None


def test_metrics_from_empty_text() -> None:
    metrics = L1Metrics.from_text("", text_area=100.0)

    assert metrics.char_density == 0.0
    assert metrics.glyph_coverage == 0.0
