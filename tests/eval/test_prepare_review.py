"""查看器产物渲染（B9）：最终 Markdown / JSON 的展示与哈希。"""

from __future__ import annotations

import json

from prepare_review import (
    _artifact_hash,
    _gap_section,
    _markdown_to_html,
    _page_json,
)


def test_markdown_to_html_keeps_tables_and_escapes_text() -> None:
    markdown = (
        "# 标题\n\n"
        "普通 <script> 文本\n\n"
        "<!--table:t001-->\n"
        "<table><tr><th>a</th></tr></table>\n"
    )

    rendered = _markdown_to_html(markdown)

    assert "<h1>标题</h1>" in rendered
    assert "<table><tr><th>a</th></tr></table>" in rendered
    assert "&lt;script&gt;" in rendered
    assert "<!--" not in rendered


def test_artifact_hash_is_stable_and_short() -> None:
    assert _artifact_hash("abc") == _artifact_hash("abc")
    assert len(_artifact_hash("abc")) == 16


def test_page_json_filters_target_page() -> None:
    payload = {
        "meta": {"delivery_level": "complete"},
        "structure": {
            "doc_id": "doc",
            "pages": [
                {"page_no": 1, "blocks": []},
                {"page_no": 2, "blocks": []},
            ],
        },
    }

    data = json.loads(_page_json(payload, 2))

    assert [page["page_no"] for page in data["structure"]["pages"]] == [2]


def test_gap_section_extracts_only_target_page() -> None:
    markdown = (
        "<!-- dataagent:gap page=3 codes=low_density -->\n> 缺口 3\n"
        "<!-- dataagent:gap page=4 codes=low_density -->\n> 缺口 4\n"
    )

    section = _gap_section(markdown, 3)

    assert section.startswith("<!-- dataagent:gap page=3")
    assert "缺口 4" not in section
    assert _gap_section(markdown, 5) == ""


def test_gap_section_stops_before_following_page() -> None:
    markdown = (
        "<!-- dataagent:gap page=3 codes=x -->\n> 缺口 3\n\n"
        "# 下一页标题\n正文\n"
        "<!-- dataagent:gap page=5 codes=x -->\n> 缺口 5\n"
    )

    section = _gap_section(markdown, 3)

    assert "缺口 3" in section
    assert "下一页标题" not in section


def test_page_json_keeps_legacy_payload() -> None:
    payload = {"doc_id": "doc", "pages": [{"page_no": 1}]}

    data = json.loads(_page_json(payload, 1))

    assert data["doc_id"] == "doc"
