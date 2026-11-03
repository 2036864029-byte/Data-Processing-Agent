"""任务状态仓储：状态机状态 + 续跑点 + 已完成文档结果。

状态与结果都在外部存储，worker/runner 重启后不依赖进程内状态。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import text

from dataagent.kernel.state import State
from dataagent.platform.store.sql.engine import Database


@runtime_checkable
class TaskStateStore(Protocol):
    """Supervisor 需要的状态仓储 + 续跑结果读写。"""

    def state(self, task_id: str) -> State: ...

    def save_state(self, task_id: str, state: State) -> None: ...

    def resume_point(self, task_id: str) -> int | None: ...

    def commit_batch(self, task_id: str, batch_index: int) -> None: ...

    def input_signature(self, task_id: str) -> str | None: ...

    def set_input_signature(self, task_id: str, signature: str) -> None: ...

    def outcomes(self, task_id: str) -> list[dict[str, Any]]: ...

    def save_outcome(
        self, task_id: str, index: int, payload: Mapping[str, Any]
    ) -> None: ...

    def reset_outcomes(self, task_id: str) -> None: ...


class InMemoryTaskStateStore:
    def __init__(self) -> None:
        self._states: dict[str, State] = {}
        self._resume: dict[str, int] = {}
        self._signatures: dict[str, str] = {}
        self._outcomes: dict[str, list[dict[str, Any]]] = {}

    def state(self, task_id: str) -> State:
        try:
            return self._states[task_id]
        except KeyError as exc:
            raise KeyError(f"任务未初始化：{task_id}") from exc

    def save_state(self, task_id: str, state: State) -> None:
        self._states[task_id] = state

    def resume_point(self, task_id: str) -> int | None:
        return self._resume.get(task_id)

    def commit_batch(self, task_id: str, batch_index: int) -> None:
        self._resume[task_id] = batch_index

    def input_signature(self, task_id: str) -> str | None:
        return self._signatures.get(task_id)

    def set_input_signature(self, task_id: str, signature: str) -> None:
        self._signatures[task_id] = signature

    def outcomes(self, task_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self._outcomes.get(task_id, ())]

    def save_outcome(
        self, task_id: str, index: int, payload: Mapping[str, Any]
    ) -> None:
        items = self._outcomes.setdefault(task_id, [])
        while len(items) <= index:
            items.append({})
        items[index] = dict(payload)

    def reset_outcomes(self, task_id: str) -> None:
        self._outcomes[task_id] = []
        self._resume[task_id] = -1


class SqlTaskStateStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    def state(self, task_id: str) -> State:
        with self._db.connect() as connection:
            row = connection.execute(
                text("SELECT state FROM task_state WHERE task_id = :task_id"),
                {"task_id": task_id},
            ).first()
        if row is None:
            raise KeyError(f"任务未初始化：{task_id}")
        return State(row.state)

    def save_state(self, task_id: str, state: State) -> None:
        with self._db.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO task_state (task_id, state, updated_at) "
                    "VALUES (:task_id, :state, now()) "
                    "ON CONFLICT (task_id) DO UPDATE SET "
                    "state = EXCLUDED.state, updated_at = now()"
                ),
                {"task_id": task_id, "state": state.value},
            )
            connection.commit()

    def resume_point(self, task_id: str) -> int | None:
        with self._db.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT resume_point FROM task_state WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            ).first()
        return None if row is None else int(row.resume_point)

    def commit_batch(self, task_id: str, batch_index: int) -> None:
        with self._db.connect() as connection:
            connection.execute(
                text(
                    "UPDATE task_state SET resume_point = :index, "
                    "updated_at = now() WHERE task_id = :task_id"
                ),
                {"task_id": task_id, "index": batch_index},
            )
            connection.commit()

    def input_signature(self, task_id: str) -> str | None:
        with self._db.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT input_signature FROM task_state "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            ).first()
        return None if row is None else str(row.input_signature)

    def set_input_signature(self, task_id: str, signature: str) -> None:
        with self._db.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO task_state (task_id, state, input_signature, "
                    "updated_at) VALUES (:task_id, :state, :signature, now()) "
                    "ON CONFLICT (task_id) DO UPDATE SET "
                    "input_signature = EXCLUDED.input_signature, "
                    "updated_at = now()"
                ),
                {
                    "task_id": task_id,
                    "state": State.RECEIVED.value,
                    "signature": signature,
                },
            )
            connection.commit()

    def outcomes(self, task_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as connection:
            row = connection.execute(
                text("SELECT outcomes FROM task_state WHERE task_id = :task_id"),
                {"task_id": task_id},
            ).first()
        return [] if row is None else list(row.outcomes or [])

    def save_outcome(
        self, task_id: str, index: int, payload: Mapping[str, Any]
    ) -> None:
        with self._db.connect() as connection:
            row = connection.execute(
                text("SELECT outcomes FROM task_state WHERE task_id = :task_id"),
                {"task_id": task_id},
            ).first()
            items: list[dict[str, Any]] = (
                list(row.outcomes or []) if row is not None else []
            )
            while len(items) <= index:
                items.append({})
            items[index] = dict(payload)
            connection.execute(
                text(
                    "UPDATE task_state SET outcomes = CAST(:outcomes AS jsonb), "
                    "updated_at = now() WHERE task_id = :task_id"
                ),
                {
                    "task_id": task_id,
                    "outcomes": json.dumps(items, ensure_ascii=False),
                },
            )
            connection.commit()

    def reset_outcomes(self, task_id: str) -> None:
        with self._db.connect() as connection:
            connection.execute(
                text(
                    "UPDATE task_state SET outcomes = CAST('[]' AS jsonb), "
                    "resume_point = -1, updated_at = now() "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
            connection.commit()
