"""交付接线测试：finalize 建块、md 来源切换、旁车释放、warnings 透出。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from dataagent.contracts.artifacts import PageArtifact
from dataagent.pipeline.merge.merger import (
    MergedDocument,
    MergedPage,
    ValidationResult,
)
from dataagent.pipeline.output.delivery import decide_delivery
from dataagent.pipeline.sniff.sniffer import Family
from dataagent.pipeline.structure.models import PageLayout
from dataagent.pipeline.table.geometry import PositionedText
from dataagent.pipeline.task.config import TaskConfig
from dataagent.pipeline.task.context import DocumentContext
from dataagent.pipeline.task.delivery import DocumentDeliveryService
from dataagent.pipeline.telemetry import TaskTelemetry


def artifact(markdown: str) -> PageArtifact:
    return PageArtifact(
        doc_id="doc",
        page_no=1,
        engine="test",
        params_hash="h",
        markdown=markdown,
    )


def table_items() -> tuple[PositionedText, ...]:
    return (
        PositionedText("a", 50, 700, 40, 10),
        PositionedText("b", 200, 700, 40, 10),
        PositionedText("1", 50, 680, 40, 10),
        PositionedText("2", 200, 680, 40, 10),
    )


def context(
    markdown: str,
    *,
    items: tuple[PositionedText, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        record=SimpleNamespace(doc_id="doc"),
        path=Path("doc.txt"),
        routing=SimpleNamespace(family=Family.TEXT),
        universe=(1,),
        processed={1},
        gaps={},
        artifacts={1: artifact(markdown)},
        reports={},
        layouts={1: PageLayout(page_no=1, items=items)},
        table_pages=set(),
        merged=None,
        delivery=None,
        outputs={},
        blocks=None,
        structure=None,
        meta=None,
    )


def service(
    tmp_path: Path, formats: tuple[str, ...] = ("markdown",)
) -> DocumentDeliveryService:
    return DocumentDeliveryService(
        output_dir=tmp_path,
        config=TaskConfig(output_formats=formats),
        telemetry=TaskTelemetry(),
    )


def test_finalize_builds_blocks_and_markdown_uses_html_table(tmp_path: Path) -> None:
    markdown = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    ctx = context(markdown, items=table_items())
    delivery = service(tmp_path)

    outcome = delivery.finalize(ctx, None)
    assert Path(outcome.outputs[0]).is_file()
    delivery.write_outputs([ctx])

    rendered = Path(outcome.outputs[0]).read_text(encoding="utf-8")
    assert "<table>" in rendered
    assert "<!--table:t001-->" in rendered
    assert ctx.blocks is not None
    assert ctx.structure is not None
    assert ctx.layouts == {}


def test_json_output_has_meta_and_structure(tmp_path: Path) -> None:
    markdown = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    ctx = context(markdown, items=table_items())
    delivery = service(tmp_path, ("json", "markdown"))

    outcome = delivery.finalize(ctx, None)
    delivery.write_outputs([ctx])

    json_path = next(path for path in outcome.outputs if path.endswith(".json"))
    md_path = next(path for path in outcome.outputs if path.endswith(".md"))
    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    assert set(payload) == {"meta", "structure"}
    assert payload["meta"]["doc_id"] == "doc"
    table = next(
        block
        for page in payload["structure"]["pages"]
        for block in page["blocks"]
        if block["type"] == "table"
    )
    assert table["table_id"] == "t001"
    assert table["rows_text"] == "a: 1；b: 2"
    rendered = Path(md_path).read_text(encoding="utf-8")
    assert "<!--table:t001-->" in rendered


def test_untrusted_table_bbox_warns_on_artifact(tmp_path: Path) -> None:
    markdown = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    ctx = context(
        markdown,
        items=(PositionedText("正文", 50, 600, 60, 10),),
    )
    delivery = service(tmp_path)

    delivery.finalize(ctx, None)

    assert "table_bbox_untrusted:1" in ctx.artifacts[1].warnings


def test_write_outputs_falls_back_when_blocks_missing(tmp_path: Path) -> None:
    document = MergedDocument(
        doc_id="doc",
        pages=(MergedPage(page_no=1, markdown="| a | b |\n| --- | --- |\n| 1 | 2 |\n"),),
        validation=ValidationResult(),
    )
    ctx = SimpleNamespace(
        merged=document,
        delivery=decide_delivery(document),
        outputs={"markdown": str(tmp_path / "doc.md")},
        blocks=None,
        structure=None,
        meta=None,
    )

    service(tmp_path).write_outputs([ctx])

    rendered = (tmp_path / "doc.md").read_text(encoding="utf-8")
    assert "| a | b |" in rendered
    assert "<!--table:" not in rendered


def test_release_payload_clears_heavy_fields() -> None:
    context = DocumentContext(
        record=SimpleNamespace(doc_id="doc"),
        path=Path("doc.txt"),
        sniff=SimpleNamespace(),
        routing=SimpleNamespace(family=Family.TEXT),
        universe=(1,),
    )
    context.merged = SimpleNamespace()
    context.blocks = SimpleNamespace()
    context.structure = SimpleNamespace()
    context.meta = {"doc_id": "doc"}
    context.layouts[1] = PageLayout(page_no=1)

    context.release_payload()

    assert context.merged is None
    assert context.blocks is None
    assert context.structure is None
    assert context.meta is None
    assert context.layouts == {}
