"""操作留痕（FR-025）：接入 / 配置 / 规则变更 / 人工修正全部可追溯。

审计记录写入 sink（P0 由调用方注入 PG sink；测试用内存 sink），
自动关联当前 span 的 trace_id，可按任务链路回放。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Any, Protocol

from dataagent.platform.observe.log import current_span


@unique
class AuditAction(StrEnum):
    INGEST = "ingest"
    CONFIG_CHANGE = "config_change"
    RULE_CHANGE = "rule_change"
    MANUAL_FIX = "manual_fix"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    record_id: str
    action: AuditAction
    actor: str
    target: str
    detail: Mapping[str, Any]
    occurred_at: str
    trace_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "action": self.action.value,
            "actor": self.actor,
            "target": self.target,
            "detail": dict(self.detail),
            "occurred_at": self.occurred_at,
            "trace_id": self.trace_id,
        }


class AuditSink(Protocol):
    def write(self, record: AuditRecord) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        self.records.append(record)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Auditor:
    def __init__(
        self, sink: AuditSink, *, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._sink = sink
        self._clock = clock

    def record(
        self, action: AuditAction, *, actor: str, target: str, **detail: Any
    ) -> AuditRecord:
        current = current_span()
        record = AuditRecord(
            record_id=uuid.uuid4().hex,
            action=action,
            actor=actor,
            target=target,
            detail=detail,
            occurred_at=self._clock().isoformat(),
            trace_id=None if current is None else current.trace_id,
        )
        self._sink.write(record)
        return record
