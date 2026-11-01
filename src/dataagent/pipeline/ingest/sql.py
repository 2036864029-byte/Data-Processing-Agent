"""接入与待确认队列的 SQL 落库（SQLAlchemy Core，禁 ORM）。

表结构由 Alembic 迁移 `0002_ingest_documents` 创建；本模块只做读写。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from dataagent.pipeline.ingest.health import HealthStatus
from dataagent.pipeline.ingest.ingestor import (
    DocumentRecord,
    PendingRecord,
)
from dataagent.platform.store.sql.engine import Database

_DOCUMENT_COLUMNS = "doc_id, source, path, size_bytes, health, family"


class SqlDocumentStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    def find(self, doc_id: str) -> DocumentRecord | None:
        with self._db.connect() as connection:
            row = connection.execute(
                text(
                    f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE doc_id = :doc_id"
                ),
                {"doc_id": doc_id},
            ).mappings().first()
        if row is None:
            return None
        return _document_from_row(row)

    def save(self, record: DocumentRecord) -> None:
        with self._db.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO documents "
                    "(doc_id, source, path, size_bytes, health, family) "
                    "VALUES (:doc_id, :source, :path, :size_bytes, :health, :family) "
                    "ON CONFLICT (doc_id) DO NOTHING"
                ),
                {
                    "doc_id": record.doc_id,
                    "source": record.source,
                    "path": record.path,
                    "size_bytes": record.size_bytes,
                    "health": record.health.value,
                    "family": record.family,
                },
            )
            connection.commit()


class SqlPendingQueue:
    def __init__(self, database: Database) -> None:
        self._db = database

    def enqueue(self, record: PendingRecord) -> None:
        with self._db.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO pending_documents "
                    "(doc_id, source, path, reason, diagnostics) "
                    "VALUES (:doc_id, :source, :path, :reason, "
                    "CAST(:diagnostics AS jsonb))"
                ),
                {
                    "doc_id": record.doc_id,
                    "source": record.source,
                    "path": record.path,
                    "reason": record.reason,
                    "diagnostics": json.dumps(
                        dict(record.diagnostics), ensure_ascii=False
                    ),
                },
            )
            connection.commit()

    def all(self) -> list[PendingRecord]:
        with self._db.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT doc_id, source, path, reason, diagnostics "
                    "FROM pending_documents ORDER BY id"
                )
            ).mappings().all()
        return [
            PendingRecord(
                doc_id=row["doc_id"],
                source=row["source"],
                path=row["path"],
                reason=row["reason"],
                diagnostics=_as_mapping(row["diagnostics"]),
            )
            for row in rows
        ]


def _document_from_row(row: Any) -> DocumentRecord:
    return DocumentRecord(
        doc_id=row["doc_id"],
        source=row["source"],
        path=row["path"],
        size_bytes=int(row["size_bytes"]),
        health=HealthStatus(row["health"]),
        family=row["family"],
    )


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return dict(value)
