"""输出落库（B7/T114 / FR-018）：页级交付产物写入数据库。

表结构由 Alembic 迁移 `0006_document_pages` 创建；按 (doc_id, page_no) upsert。
缺口页同样落库（gap = true），不得跳过。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.merge.merger import MergedDocument
from dataagent.pipeline.output.delivery import DeliveryDecision
from dataagent.platform.store.sql.engine import Database


@dataclass(frozen=True, slots=True)
class StoredPage:
    doc_id: str
    page_no: int
    gap: bool
    extract_quality: float | None
    markdown: str
    defect_codes: tuple[DefectCode, ...]
    delivery_level: str


class SqlDocumentOutputStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    def save(self, document: MergedDocument, decision: DeliveryDecision) -> None:
        with self._db.connect() as connection:
            for page in document.pages:
                connection.execute(
                    text(
                        "INSERT INTO document_pages "
                        "(doc_id, page_no, gap, extract_quality, markdown, "
                        "defect_codes, delivery_level) "
                        "VALUES (:doc_id, :page_no, :gap, :extract_quality, "
                        ":markdown, CAST(:defect_codes AS jsonb), :delivery_level) "
                        "ON CONFLICT (doc_id, page_no) DO UPDATE SET "
                        "gap = EXCLUDED.gap, "
                        "extract_quality = EXCLUDED.extract_quality, "
                        "markdown = EXCLUDED.markdown, "
                        "defect_codes = EXCLUDED.defect_codes, "
                        "delivery_level = EXCLUDED.delivery_level, "
                        "updated_at = now()"
                    ),
                    {
                        "doc_id": document.doc_id,
                        "page_no": page.page_no,
                        "gap": page.gap,
                        "extract_quality": page.extract_quality,
                        "markdown": page.markdown,
                        "defect_codes": json.dumps(
                            [code.value for code in page.defect_codes],
                            ensure_ascii=False,
                        ),
                        "delivery_level": decision.level.value,
                    },
                )
            connection.commit()

    def load(self, doc_id: str) -> tuple[StoredPage, ...]:
        with self._db.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT doc_id, page_no, gap, extract_quality, markdown, "
                    "defect_codes, delivery_level FROM document_pages "
                    "WHERE doc_id = :doc_id ORDER BY page_no"
                ),
                {"doc_id": doc_id},
            ).mappings().all()
        return tuple(
            StoredPage(
                doc_id=row["doc_id"],
                page_no=row["page_no"],
                gap=row["gap"],
                extract_quality=row["extract_quality"],
                markdown=row["markdown"],
                defect_codes=tuple(
                    DefectCode(code) for code in _as_list(row["defect_codes"])
                ),
                delivery_level=row["delivery_level"],
            )
            for row in rows
        )


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return [str(item) for item in parsed]
    return [str(item) for item in value]
