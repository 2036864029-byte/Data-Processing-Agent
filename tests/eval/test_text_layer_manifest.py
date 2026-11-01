"""黄金集文本层口径测试（T134，Phase 12 R4）。"""

from _lib import classify_text_layer, text_layer_summary


def test_text_layer_classification() -> None:
    assert (
        classify_text_layer(needs_ocr=False, has_text=True, probe_usable=False)
        == "present"
    )
    assert (
        classify_text_layer(needs_ocr=False, has_text=False, probe_usable=False)
        == "absent"
    )
    assert (
        classify_text_layer(needs_ocr=True, has_text=False, probe_usable=True)
        == "invisible_text_layer"
    )
    assert (
        classify_text_layer(needs_ocr=True, has_text=False, probe_usable=False)
        == "pure_scan"
    )


def test_text_layer_summary_counts_and_rate() -> None:
    rows = [
        {"text_layer": "present"},
        {"text_layer": "invisible_text_layer"},
        {"text_layer": "invisible_text_layer"},
        {"text_layer": "pure_scan"},
        {"text_layer": "absent"},
    ]

    summary = text_layer_summary(rows)

    assert summary["counts"]["invisible_text_layer"] == 2
    assert summary["counts"]["pure_scan"] == 1
    assert summary["recovery_rate"] == 2 / 3
    assert summary["ocr_candidates"] == 3


def test_garbled_text_layer_classification() -> None:
    assert (
        classify_text_layer(
            needs_ocr=True,
            has_text=False,
            probe_usable=True,
            reason="suspected_garbled_text",
        )
        == "garbled_text_layer"
    )
    rows = [
        {"text_layer": "invisible_text_layer"},
        {"text_layer": "garbled_text_layer"},
        {"text_layer": "pure_scan"},
    ]

    summary = text_layer_summary(rows)

    assert summary["counts"]["garbled_text_layer"] == 1
    assert summary["recovery_rate"] == 2 / 3
