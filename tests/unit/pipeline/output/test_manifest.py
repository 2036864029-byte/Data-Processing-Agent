"""交付清单测试：一行一文档、相对产物 key、失败/重复文档。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dataagent.pipeline.output.delivery import DeliveryLevel
from dataagent.pipeline.output.manifest import (
    manifest_entry,
    verify_manifest,
    write_manifest,
)
from dataagent.pipeline.task.results import DocumentOutcome

DOC_ID = "a" * 64
OTHER = "b" * 64


def processed(root: Path) -> DocumentOutcome:
    return DocumentOutcome(
        doc_id=DOC_ID,
        path="src.pdf",
        family="pdf",
        status="processed",
        pages=10,
        gap_pages=1,
        level=DeliveryLevel.NEEDS_REVIEW,
        outputs=(
            str(root / "md" / "aa" / f"{DOC_ID}.md"),
            str(root / "json" / "aa" / f"{DOC_ID}.json"),
            str(root / "tables" / "aa" / f"{DOC_ID}.tables.json"),
        ),
    )


def test_manifest_entry_uses_relative_keys(tmp_path: Path) -> None:
    entry = manifest_entry(processed(tmp_path), tmp_path)

    assert entry["doc_id"] == DOC_ID
    assert entry["md"] == f"md/aa/{DOC_ID}.md"
    assert entry["json"] == f"json/aa/{DOC_ID}.json"
    assert entry["tables"] == f"tables/aa/{DOC_ID}.tables.json"
    assert entry["delivery_level"] == "needs_review"
    assert entry["pages"] == 10
    assert entry["gap_pages"] == 1


def test_manifest_line_per_document_including_failed_and_duplicate(
    tmp_path: Path,
) -> None:
    processed_outcome = processed(tmp_path)
    failed = DocumentOutcome(
        doc_id=OTHER,
        path="bad.pdf",
        family="pdf",
        status="failed",
        pages=5,
        gap_pages=5,
    )
    duplicate = DocumentOutcome(
        doc_id=DOC_ID,
        path="dup.pdf",
        family="pdf",
        status="duplicate",
        pages=10,
        gap_pages=1,
        level=DeliveryLevel.NEEDS_REVIEW,
        outputs=processed_outcome.outputs,
    )

    path = write_manifest([processed_outcome, failed, duplicate], tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 3
    entries = [json.loads(line) for line in lines]
    assert entries[0]["md"] == f"md/aa/{DOC_ID}.md"
    assert entries[1]["status"] == "failed"
    assert entries[1]["md"] is None
    assert entries[1]["json"] is None
    assert entries[1]["delivery_level"] is None
    assert entries[2]["status"] == "duplicate"
    assert entries[2]["md"] == entries[0]["md"]


def test_empty_manifest_file_is_written(tmp_path: Path) -> None:
    path = write_manifest([], tmp_path)

    assert path.is_file()
    assert path.read_text(encoding="utf-8") == ""


def _land_outputs(outcome: DocumentOutcome) -> None:
    for output in outcome.outputs:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}")


def test_verify_manifest_passes_when_keys_match_files(tmp_path: Path) -> None:
    outcome = processed(tmp_path)
    _land_outputs(outcome)
    write_manifest([outcome], tmp_path)

    report = verify_manifest(tmp_path)

    assert report.ok
    assert report.documents == 1
    assert report.keys_checked == 3
    assert report.missing == ()
    assert report.orphans == ()


def test_verify_manifest_reports_missing_output(tmp_path: Path) -> None:
    outcome = processed(tmp_path)
    _land_outputs(outcome)
    write_manifest([outcome], tmp_path)
    (tmp_path / "json" / "aa" / f"{DOC_ID}.json").unlink()

    report = verify_manifest(tmp_path)

    assert not report.ok
    assert report.missing == (f"json/aa/{DOC_ID}.json",)


def test_verify_manifest_reports_orphan_output(tmp_path: Path) -> None:
    outcome = processed(tmp_path)
    _land_outputs(outcome)
    write_manifest([outcome], tmp_path)
    orphan = tmp_path / "md" / "zz" / "orphan.md"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")

    report = verify_manifest(tmp_path)

    assert not report.ok
    assert report.orphans == ("md/zz/orphan.md",)


def test_verify_manifest_requires_manifest_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        verify_manifest(tmp_path)


def test_verify_manifest_treats_escaping_key_as_missing(tmp_path: Path) -> None:
    (tmp_path / "manifest.jsonl").write_text(
        json.dumps({"doc_id": "x", "md": "../outside.md"}) + "\n",
        encoding="utf-8",
    )

    report = verify_manifest(tmp_path)

    assert report.missing == ("../outside.md",)


def test_verify_manifest_rejects_malformed_line(tmp_path: Path) -> None:
    (tmp_path / "manifest.jsonl").write_text("not json\n", encoding="utf-8")

    with pytest.raises(ValueError):
        verify_manifest(tmp_path)


def test_verify_manifest_rejects_non_object_line(tmp_path: Path) -> None:
    (tmp_path / "manifest.jsonl").write_text("[]\n", encoding="utf-8")

    with pytest.raises(ValueError):
        verify_manifest(tmp_path)
