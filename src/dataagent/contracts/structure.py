"""文档结构契约：页面由有序块组成，表格块持 TableGrid 而非渲染文本。

这是入库与人工视图的共同真源：机器直接消费结构，人类视图由结构渲染。
PR-2：块 ID / 表 ID / 标题层级 / rows_text 进入契约。
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dataagent.contracts.table import TableGrid

_BLOCK_ID_RE = re.compile(r"^b\d{4,}$")
_TABLE_ID_RE = re.compile(r"^t\d{3,}$")


class TextBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["text"] = "text"
    block_id: str
    page_no: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    text: str
    role: str = "body"
    level: int | None = Field(default=None, ge=1, le=6)

    @field_validator("block_id")
    @classmethod
    def _block_id_format(cls, value: str) -> str:
        if not _BLOCK_ID_RE.match(value):
            raise ValueError("block_id 必须为 b + 至少四位数字")
        return value


class TableBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["table"] = "table"
    block_id: str
    page_no: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    grid: TableGrid
    table_id: str
    caption: str | None = None
    rows_text: str = ""

    @field_validator("block_id")
    @classmethod
    def _block_id_format(cls, value: str) -> str:
        if not _BLOCK_ID_RE.match(value):
            raise ValueError("block_id 必须为 b + 至少四位数字")
        return value

    @field_validator("table_id")
    @classmethod
    def _table_id_format(cls, value: str) -> str:
        if not _TABLE_ID_RE.match(value):
            raise ValueError("table_id 必须为 t + 至少三位数字")
        return value

    @model_validator(mode="after")
    def _rows_text_present(self) -> Self:
        has_data = any(
            cell.text.strip()
            for cell in self.grid.cells
            if cell.row >= self.grid.header_rows
        )
        if has_data and not self.rows_text.strip():
            raise ValueError("表格块必须携带 rows_text")
        return self


Block = Annotated[TextBlock | TableBlock, Field(discriminator="type")]


class PageStructure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page_no: int = Field(ge=1)
    blocks: tuple[Block, ...] = ()

    @model_validator(mode="after")
    def _page_consistency(self) -> Self:
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("同页 block_id 不得重复")
        for block in self.blocks:
            if block.page_no != self.page_no:
                raise ValueError(
                    f"块页码 {block.page_no} 与页面 {self.page_no} 不一致"
                )
        return self


class DocumentStructure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    pages: tuple[PageStructure, ...] = ()

    @model_validator(mode="after")
    def _page_order(self) -> Self:
        page_numbers = [page.page_no for page in self.pages]
        if page_numbers != sorted(page_numbers):
            raise ValueError("页面必须按页码升序排列")
        if len(page_numbers) != len(set(page_numbers)):
            raise ValueError("页码不得重复")
        block_ids = [
            block.block_id for page in self.pages for block in page.blocks
        ]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block_id 在文档内必须唯一")
        table_ids = [
            block.table_id
            for page in self.pages
            for block in page.blocks
            if isinstance(block, TableBlock)
        ]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("table_id 在文档内必须唯一")
        return self
