"""文档结构契约：页面由有序块组成，表格块持 TableGrid 而非渲染文本。

这是入库与人工视图的共同真源：机器直接消费结构，人类视图由结构渲染。
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataagent.contracts.table import TableGrid


class TextBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["text"] = "text"
    page_no: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    text: str
    role: str = "body"


class TableBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["table"] = "table"
    page_no: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    grid: TableGrid


Block = Annotated[TextBlock | TableBlock, Field(discriminator="type")]


class PageStructure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page_no: int = Field(ge=1)
    blocks: tuple[Block, ...] = ()

    @model_validator(mode="after")
    def _page_consistency(self) -> Self:
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
        return self
