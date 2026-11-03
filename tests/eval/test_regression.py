"""质量回归脚本测试：指标口径、对比方向、基线读取。"""

from __future__ import annotations

from pathlib import Path

from _lib import write_csv, write_json
from regression import compare, compute_metrics, markdown_metrics


def test_markdown_metrics_detects_artifacts() -> None:
    markdown = (
        "第一段没有句号\n"
        "第二段也没有句号\n"
        "第二十八条 采购人应当\n"
        "（一）提交材料。\n"
        "依 法审核管理。\n"
    )

    metrics = markdown_metrics(markdown)

    assert metrics["hard_wrap_ratio"] > 0
    assert metrics["phantom_cjk_space_rate"] > 0
    assert metrics["numbering_per_1000"] > 0
    assert metrics["prose_ratio"] == 1.0


def test_markdown_metrics_table() -> None:
    markdown = "| 项目 | 金额 |\n|---|---|\n| a | 1 |\n"

    metrics = markdown_metrics(markdown)

    assert metrics["table_cell_fill"] == 1.0
    assert metrics["table_header_ok"] == 1.0
    assert metrics["prose_ratio"] == 0.0


def test_compare_directions_and_tolerance() -> None:
    baseline = {
        "overall": {
            "phantom_cjk_space_rate": 10.0,
            "hard_wrap_ratio": 0.5,
            "table_cell_fill": 0.9,
            "numbering_per_1000": 0.2,
        }
    }
    current = {
        "overall": {
            "phantom_cjk_space_rate": 2.0,
            "hard_wrap_ratio": 0.7,
            "table_cell_fill": 0.95,
            "numbering_per_1000": 0.1,
        }
    }

    result = {item.metric: item.status for item in compare(baseline, current)}

    assert result["phantom_cjk_space_rate"] == "improved"
    assert result["hard_wrap_ratio"] == "regressed"
    assert result["table_cell_fill"] == "improved"
    assert result["numbering_per_1000"] == "regressed"


def test_compute_metrics_reads_human_sheet(tmp_path: Path) -> None:
    (tmp_path / "parsed").mkdir()
    (tmp_path / "parsed" / "R-0001.md").write_text(
        "依 法审核。\n", encoding="utf-8"
    )
    write_csv(
        tmp_path / "human_sheet.csv",
        [
            {
                "sample_id": "R-0001",
                "category": "text_only",
                "file": "x.pdf",
                "page_no": "1",
            }
        ],
        ("sample_id", "category", "file", "page_no"),
    )
    write_json(
        tmp_path / "manifest.json",
        [{"sample_id": "R-0001", "text_layer": "present"}],
    )

    metrics = compute_metrics(golden_root=tmp_path)

    assert metrics["samples"]["R-0001"]["phantom_cjk_space_rate"] > 0
    assert metrics["overall"]["prose_ratio"] == 1.0
