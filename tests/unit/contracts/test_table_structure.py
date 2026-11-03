"""TableGrid / DocumentStructure 契约测试：边界、重叠、块类型与页面顺序。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dataagent.contracts.structure import (
    DocumentStructure,
    PageStructure,
    TableBlock,
    TextBlock,
)
from dataagent.contracts.table import TableCell, TableGrid


def grid(**overrides: object) -> TableGrid:
    values: dict[str, object] = {
        "page_no": 1,
        "bbox": (0.0, 0.0, 100.0, 50.0),
        "n_rows": 2,
        "n_cols": 2,
        "header_rows": 1,
        "cells": (
            TableCell(row=0, col=0, text="项目"),
            TableCell(row=0, col=1, text="金额"),
            TableCell(row=1, col=0, text="合计"),
            TableCell(row=1, col=1, text="100"),
        ),
        "source_engine": "test",
        "confidence": 0.9,
    }
    values.update(overrides)
    return TableGrid(**values)  # type: ignore[arg-type]


def test_valid_grid_roundtrip() -> None:
    value = grid()

    assert TableGrid.model_validate_json(value.model_dump_json()) == value


def test_invalid_bbox_rejected() -> None:
    with pytest.raises(ValidationError):
        grid(bbox=(10.0, 0.0, 5.0, 50.0))


def test_cell_out_of_bounds_rejected() -> None:
    with pytest.raises(ValidationError):
        grid(cells=(TableCell(row=1, col=0, text="x", rowspan=2),))


def test_overlapping_cells_rejected() -> None:
    with pytest.raises(ValidationError):
        grid(
            cells=(
                TableCell(row=0, col=0, text="a", colspan=2),
                TableCell(row=0, col=1, text="b"),
            )
        )


def test_header_rows_cannot_exceed_total() -> None:
    with pytest.raises(ValidationError):
        grid(header_rows=3)


def test_document_structure_discriminates_blocks() -> None:
    page = PageStructure(
        page_no=1,
        blocks=(
            TextBlock(page_no=1, bbox=(0, 0, 100, 20), text="正文"),
            TableBlock(page_no=1, bbox=(0, 20, 100, 60), grid=grid()),
        ),
    )
    document = DocumentStructure(doc_id="d1", pages=(page,))

    restored = DocumentStructure.model_validate_json(document.model_dump_json())

    assert isinstance(restored.pages[0].blocks[0], TextBlock)
    assert isinstance(restored.pages[0].blocks[1], TableBlock)


def test_page_block_must_match_page_number() -> None:
    with pytest.raises(ValidationError):
        PageStructure(
            page_no=1,
            blocks=(TextBlock(page_no=2, bbox=(0, 0, 1, 1), text="x"),),
        )


def test_pages_must_be_sorted_and_unique() -> None:
    page1 = PageStructure(page_no=1)
    page2 = PageStructure(page_no=2)

    assert DocumentStructure(doc_id="d", pages=(page1, page2)).pages
    with pytest.raises(ValidationError):
        DocumentStructure(doc_id="d", pages=(page2, page1))
    with pytest.raises(ValidationError):
        DocumentStructure(doc_id="d", pages=(page1, page1))
