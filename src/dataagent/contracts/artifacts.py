"""插件产物契约：PDF 分类结果 / 页产物（data-model.md PageArtifact、contracts/plugin-manifest.md）。

- `pdf.classify` 不产内容：`markdown` 恒为 None、`cmap_gaps` 恒为空（验收 2）。
- `pdf.text.extract` 产不可变 artifact：模型 frozen，任何字段不可原地修改。
- 页码一律 1-indexed；插件原始 0-indexed 字段在适配器内经 contracts/pages.py 转换。

契约层叶子模块（仅依赖 .policies）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dataagent.contracts.policies import PdfType


def _validate_pages(values: tuple[int, ...]) -> tuple[int, ...]:
    for value in values:
        if value < 1:
            raise ValueError(f"页码必须 ≥ 1（内部一律 1-indexed），收到 {value}")
    return values


class FontCMapGaps(BaseModel):
    """字体 ToUnicode CMap 缺口（detect_pdf 阶段恒为空）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    font: str
    codes: int = Field(ge=0)
    interpolated: int = Field(ge=0)
    unmapped: int = Field(ge=0)


class PageOcrReason(BaseModel):
    """逐页 OCR 路由原因：机器可读标识原样保留（G2）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page: int = Field(ge=1, description="内部页码，1-indexed")
    reasons: tuple[str, ...] = ()


class PdfClassification(BaseModel):
    """`pdf.classification.v1`：文档级分类结果（路由消费，不产内容）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pdf_type: PdfType
    confidence: float = Field(ge=0.0, le=1.0)
    page_count: int = Field(ge=0)
    pages_needing_ocr: tuple[int, ...] = ()
    ocr_reasons_by_page: tuple[PageOcrReason, ...] = ()
    pages_with_tables: tuple[int, ...] = ()
    pages_with_columns: tuple[int, ...] = ()
    is_complex_layout: bool = False
    has_encoding_issues: bool = False
    cmap_gaps: tuple[FontCMapGaps, ...] = ()
    markdown: str | None = Field(
        default=None, description="分类不产内容，恒为 None（plugin-manifest 验收 2）"
    )

    @field_validator("pages_needing_ocr", "pages_with_tables", "pages_with_columns")
    @classmethod
    def _pages_are_one_indexed(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        return _validate_pages(values)


class PageArtifact(BaseModel):
    """`page.artifact.v1`：单页解析产物（不可变、内容寻址）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    page_no: int = Field(ge=1, description="内部页码，1-indexed")
    engine: str
    params_hash: str
    markdown: str
    needs_ocr: bool = False
    ocr_reason: str | None = None


class ExtractionResult(BaseModel):
    """一次批级抽取的完整产物集合（保持调用方给定页顺序）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    engine: str
    params_hash: str
    pages: tuple[PageArtifact, ...] = ()
    pages_with_tables: tuple[int, ...] = ()
    pages_with_columns: tuple[int, ...] = ()
    pages_needing_ocr: tuple[int, ...] = ()
    ocr_reasons_by_page: tuple[PageOcrReason, ...] = ()
    is_complex: bool = False

    @field_validator("pages_with_tables", "pages_with_columns", "pages_needing_ocr")
    @classmethod
    def _pages_are_one_indexed(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        return _validate_pages(values)
