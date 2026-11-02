"""任务进度存储：runner 写入、status 只读（不持有进程内任务状态）。

- `TaskProgressStore` 抽象：upsert + 最近任务查询；
- `SqlTaskProgressStore`：PG 单表 upsert（短事务，低频写）；
- `InMemoryTaskProgressStore`：测试 / 单进程演示用。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import text

from dataagent.platform.store.sql.engine import Database


@dataclass(frozen=True, slots=True)
class TaskProgress:
    task_id: str
    state: str
    progress: str
    pages_total: int = 0
    pages_done: int = 0
    detail: Mapping[str, Any] = field(default_factory=dict)
    updated_at: str = ""


class TaskProgressStore(Protocol):
    def upsert(self, progress: TaskProgress) -> None: ...

    def recent(self, limit: int = 50) -> list[TaskProgress]: ...


class InMemoryTaskProgressStore:
    def __init__(self) -> None:
        self._items: dict[str, TaskProgress] = {}

    def upsert(self, progress: TaskProgress) -> None:
        stamped = TaskProgress(
            task_id=progress.task_id,
            state=progress.state,
            progress=progress.progress,
            pages_total=progress.pages_total,
            pages_done=progress.pages_done,
            detail=dict(progress.detail),
            updated_at=datetime.now(UTC).isoformat(),
        )
        # 重新插入保证“最近更新”顺序
        self._items.pop(progress.task_id, None)
        self._items[progress.task_id] = stamped

    def recent(self, limit: int = 50) -> list[TaskProgress]:
        return list(reversed(self._items.values()))[: max(0, limit)]


class SqlTaskProgressStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    def upsert(self, progress: TaskProgress) -> None:
        with self._db.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO task_progress "
                    "(task_id, state, progress, pages_total, pages_done, "
                    "detail, updated_at) "
                    "VALUES (:task_id, :state, :progress, :pages_total, "
                    ":pages_done, CAST(:detail AS jsonb), now()) "
                    "ON CONFLICT (task_id) DO UPDATE SET "
                    "state = EXCLUDED.state, "
                    "progress = EXCLUDED.progress, "
                    "pages_total = EXCLUDED.pages_total, "
                    "pages_done = EXCLUDED.pages_done, "
                    "detail = EXCLUDED.detail, "
                    "updated_at = now()"
                ),
                {
                    "task_id": progress.task_id,
                    "state": progress.state,
                    "progress": progress.progress,
                    "pages_total": progress.pages_total,
                    "pages_done": progress.pages_done,
                    "detail": json.dumps(
                        dict(progress.detail), ensure_ascii=False
                    ),
                },
            )
            connection.commit()

    def recent(self, limit: int = 50) -> list[TaskProgress]:
        with self._db.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT task_id, state, progress, pages_total, pages_done, "
                    "detail, updated_at FROM task_progress "
                    "ORDER BY updated_at DESC, task_id LIMIT :limit"
                ),
                {"limit": max(0, limit)},
            )
            return [
                TaskProgress(
                    task_id=row.task_id,
                    state=row.state,
                    progress=row.progress,
                    pages_total=row.pages_total,
                    pages_done=row.pages_done,
                    detail=dict(row.detail or {}),
                    updated_at=(
                        row.updated_at.isoformat() if row.updated_at else ""
                    ),
                )
                for row in rows
            ]
