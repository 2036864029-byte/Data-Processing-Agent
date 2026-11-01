"""页面画像落库（SQLAlchemy Core，禁 ORM）。

表结构由 Alembic 迁移 `0004_page_profiles` 创建；两段写入按 (doc_id, page_no) upsert。
`class` / `layout` 路由段先写，`signals` / `quality` 质量段解析后写。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from dataagent.contracts.policies import PageProfile
from dataagent.platform.store.sql.engine import Database


class SqlProfileStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    def load(self, doc_id: str, page_no: int) -> PageProfile | None:
        with self._db.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT doc_id, page_no, class, layout, signals, quality "
                    "FROM page_profiles WHERE doc_id = :doc_id AND page_no = :page_no"
                ),
                {"doc_id": doc_id, "page_no": page_no},
            ).mappings().first()
        if row is None:
            return None
        return PageProfile.model_validate(
            {
                "doc_id": row["doc_id"],
                "page_no": row["page_no"],
                "class": _as_mapping(row["class"]),
                "layout": _as_mapping(row["layout"]),
                "signals": _as_optional_mapping(row["signals"]),
                "quality": _as_optional_mapping(row["quality"]),
            }
        )

    def save(self, profile: PageProfile) -> None:
        route = profile.model_dump(by_alias=True, mode="json")
        with self._db.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO page_profiles "
                    "(doc_id, page_no, class, layout, signals, quality) "
                    "VALUES (:doc_id, :page_no, CAST(:class AS jsonb), "
                    "CAST(:layout AS jsonb), CAST(:signals AS jsonb), "
                    "CAST(:quality AS jsonb)) "
                    "ON CONFLICT (doc_id, page_no) DO UPDATE SET "
                    "class = EXCLUDED.class, layout = EXCLUDED.layout, "
                    "signals = EXCLUDED.signals, quality = EXCLUDED.quality, "
                    "updated_at = now()"
                ),
                {
                    "doc_id": profile.doc_id,
                    "page_no": profile.page_no,
                    "class": json.dumps(route["class"], ensure_ascii=False),
                    "layout": json.dumps(route["layout"], ensure_ascii=False),
                    "signals": _dump_optional(profile.signals),
                    "quality": _dump_optional(profile.quality),
                },
            )
            connection.commit()


def _dump_optional(model: Any) -> str | None:
    if model is None:
        return None
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False)


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return dict(value)


def _as_optional_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return _as_mapping(value)
