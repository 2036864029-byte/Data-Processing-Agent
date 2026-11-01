"""路由决策落库（SQLAlchemy Core，禁 ORM）。

表结构由 Alembic 迁移 `0003_route_decisions` 创建；本模块只做读写。
每条决策含机器可读理由与决策时画像快照（FR-004）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from dataagent.contracts.policies import ChainId
from dataagent.pipeline.route.routes import RouteDecision, RouteLevel
from dataagent.platform.store.sql.engine import Database


class SqlRouteDecisionStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    def save(self, decision: RouteDecision) -> None:
        with self._db.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO route_decisions "
                    "(doc_id, level, page_no, chain, reasons, profile_snapshot) "
                    "VALUES (:doc_id, :level, :page_no, :chain, "
                    "CAST(:reasons AS jsonb), CAST(:profile_snapshot AS jsonb))"
                ),
                {
                    "doc_id": decision.doc_id,
                    "level": decision.level.value,
                    "page_no": decision.page_no,
                    "chain": None if decision.chain is None else decision.chain.value,
                    "reasons": json.dumps(list(decision.reasons), ensure_ascii=False),
                    "profile_snapshot": json.dumps(
                        dict(decision.profile_snapshot), ensure_ascii=False
                    ),
                },
            )
            connection.commit()

    def for_doc(self, doc_id: str) -> list[RouteDecision]:
        with self._db.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT doc_id, level, page_no, chain, reasons, profile_snapshot "
                    "FROM route_decisions WHERE doc_id = :doc_id ORDER BY id"
                ),
                {"doc_id": doc_id},
            ).mappings().all()
        return [
            RouteDecision(
                doc_id=row["doc_id"],
                level=RouteLevel(row["level"]),
                page_no=row["page_no"],
                chain=None if row["chain"] is None else ChainId(row["chain"]),
                reasons=tuple(_as_list(row["reasons"])),
                profile_snapshot=_as_mapping(row["profile_snapshot"]),
            )
            for row in rows
        ]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return [str(item) for item in parsed]
    return [str(item) for item in value]


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return dict(value)
