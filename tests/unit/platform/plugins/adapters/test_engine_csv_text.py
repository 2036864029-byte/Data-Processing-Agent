"""engine.csv / engine.text 契约接入测试（T087，B3）。

CSV → `tabular.text.extract`；TXT → `text.text.extract`；产出不可变 artifact。
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dataagent.contracts.artifacts import ExtractionResult
from dataagent.contracts.plugin import PluginContext, PluginKind
from dataagent.platform.plugins.adapters.engine_csv import PLUGIN as CSV_PLUGIN
from dataagent.platform.plugins.adapters.engine_text import PLUGIN as TEXT_PLUGIN

CONTEXT = PluginContext(task_id="task-1", doc_id="b" * 64)


def test_csv_manifest_declares_tabular_extract() -> None:
    manifest = CSV_PLUGIN.manifest

    assert manifest.kind is PluginKind.ENGINE
    assert [signature.capability for signature in manifest.provides] == [
        "tabular.text.extract"
    ]


def test_text_manifest_declares_text_extract() -> None:
    manifest = TEXT_PLUGIN.manifest

    assert manifest.kind is PluginKind.ENGINE
    assert [signature.capability for signature in manifest.provides] == [
        "text.text.extract"
    ]


def test_csv_extraction_returns_markdown_table(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("name,amount\nAlice,10\nBob,20\n", encoding="utf-8")

    raw = CSV_PLUGIN.invoke(
        "tabular.text.extract", {"path": str(path)}, CONTEXT
    )
    result = ExtractionResult.model_validate(raw)

    assert len(result.pages) == 1
    artifact = result.pages[0]
    assert artifact.page_no == 1
    assert artifact.engine == CSV_PLUGIN.manifest.ref
    assert "| name | amount |" in artifact.markdown
    assert "| Alice | 10 |" in artifact.markdown
    assert "| Bob | 20 |" in artifact.markdown


def test_csv_extraction_honours_explicit_delimiter(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("a;b;c\n1;2;3\n", encoding="utf-8")

    raw = CSV_PLUGIN.invoke(
        "tabular.text.extract", {"path": str(path), "delimiter": ";"}, CONTEXT
    )
    result = ExtractionResult.model_validate(raw)

    assert "| a | b | c |" in result.pages[0].markdown


def test_csv_artifact_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")

    result = ExtractionResult.model_validate(
        CSV_PLUGIN.invoke("tabular.text.extract", {"path": str(path)}, CONTEXT)
    )

    with pytest.raises(ValidationError):
        result.pages[0].markdown = "tampered"  # type: ignore[misc]


def test_text_extraction_preserves_content(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    text = "第一行\n第二行\n"
    path.write_bytes(text.encode("utf-8"))

    raw = TEXT_PLUGIN.invoke("text.text.extract", {"path": str(path)}, CONTEXT)
    result = ExtractionResult.model_validate(raw)

    assert len(result.pages) == 1
    assert result.pages[0].page_no == 1
    assert result.pages[0].markdown == text
    assert result.pages[0].engine == TEXT_PLUGIN.manifest.ref


def test_unknown_capability_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("x\n", encoding="utf-8")

    with pytest.raises(KeyError):
        TEXT_PLUGIN.invoke("text.text.classify", {"path": str(path)}, CONTEXT)
