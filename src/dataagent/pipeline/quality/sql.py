"""质检报告落库（SQLAlchemy Core，禁 ORM）。

表结构由 Alembic 迁移 `0005_page_reports` 创建；按 (doc_id, page_no) upsert。
质检结果先落库再触发下游（G5）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from dataagent.contracts.messages import PageReport
from dataagent.platform.store.sql.engine import Database


class SqlQualityResultStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    def save(self, report: PageReport) -> None:
        with self._db.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO page_reports "
                    "(doc_id, page_no, verdict, extract_quality, defects, "
                    "attribution, recommended_action, qc_confidence) "
                    "VALUES (:doc_id, :page_no, :verdict, :extract_quality, "
                    "CAST(:defects AS jsonb), :attribution, :recommended_action, "
                    ":qc_confidence) "
                    "ON CONFLICT (doc_id, page_no) DO UPDATE SET "
                    "verdict = EXCLUDED.verdict, "
                    "extract_quality = EXCLUDED.extract_quality, "
                    "defects = EXCLUDED.defects, "
                    "attribution = EXCLUDED.attribution, "
                    "recommended_action = EXCLUDED.recommended_action, "
                    "qc_confidence = EXCLUDED.qc_confidence, "
                    "updated_at = now()"
                ),
                {
                    "doc_id": report.doc_id,
                    "page_no": report.page_no,
                    "verdict": report.verdict.value,
                    "extract_quality": report.extract_quality,
                    "defects": json.dumps(
                        [defect.model_dump(mode="json") for defect in report.defects],
                        ensure_ascii=False,
                    ),
                    "attribution": report.attribution,
                    "recommended_action": (
                        None
                        if report.recommended_action is None
                        else report.recommended_action.value
                    ),
                    "qc_confidence": report.qc_confidence,
                },
            )
            connection.commit()

    def load(self, doc_id: str, page_no: int) -> PageReport | None:
        with self._db.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT doc_id, page_no, verdict, extract_quality, defects, "
                    "attribution, recommended_action, qc_confidence "
                    "FROM page_reports WHERE doc_id = :doc_id AND page_no = :page_no"
                ),
                {"doc_id": doc_id, "page_no": page_no},
            ).mappings().first()
        if row is None:
            return None
        return PageReport.model_validate(
            {
                "doc_id": row["doc_id"],
                "page_no": row["page_no"],
                "verdict": row["verdict"],
                "extract_quality": row["extract_quality"],
                "defects": _as_list(row["defects"]),
                "attribution": row["attribution"],
                "recommended_action": row["recommended_action"],
                "qc_confidence": row["qc_confidence"],
            }
        )


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return list(parsed) if isinstance(parsed, list) else []
    return list(value)
