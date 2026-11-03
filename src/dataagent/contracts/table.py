"""表格结构化契约：TableGrid 是表格的唯一真源，Markdown 只是渲染视图。

坐标约定：bbox = (x0, y0, x1, y1)，x0 < x1、y0 < y1；页码一律内部 1-indexed。
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TableCell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    row: int = Field(ge=0)
    col: int = Field(ge=0)
    text: str
    rowspan: int = Field(default=1, ge=1)
    colspan: int = Field(default=1, ge=1)
    bbox: tuple[float, float, float, float] | None = None


class TableGrid(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page_no: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    n_rows: int = Field(ge=1)
    n_cols: int = Field(ge=1)
    header_rows: int = Field(default=1, ge=0)
    cells: tuple[TableCell, ...] = ()
    source_engine: str
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_grid(self) -> Self:
        x0, y0, x1, y1 = self.bbox
        if x0 >= x1 or y0 >= y1:
            raise ValueError(f"bbox 必须满足 x0<x1、y0<y1：{self.bbox}")
        if self.header_rows > self.n_rows:
            raise ValueError("header_rows 不得超过总行数")
        occupied: set[tuple[int, int]] = set()
        for cell in self.cells:
            if cell.row + cell.rowspan > self.n_rows:
                raise ValueError(f"单元格越界（行）：{cell}")
            if cell.col + cell.colspan > self.n_cols:
                raise ValueError(f"单元格越界（列）：{cell}")
            for row in range(cell.row, cell.row + cell.rowspan):
                for col in range(cell.col, cell.col + cell.colspan):
                    if (row, col) in occupied:
                        raise ValueError(f"单元格重叠：{row},{col}")
                    occupied.add((row, col))
        return self
