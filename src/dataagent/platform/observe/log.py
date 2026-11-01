"""结构化日志 + span 上下文（FR-025 / PRD 4.9.1）。

一条任务链路一个 trace_id，每个阶段一个 span；日志为 JSON lines，
自动携带当前 span 的字段（task_id / doc_id / page_no / stage / engine / ...）。
不依赖外部观测服务，完全离线可用。
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

LOGGER_NAME: Final[str] = "dataagent"

# Trace 字段要求（PRD 4.9.1 逐字）
TRACE_FIELDS: Final[tuple[str, ...]] = (
    "task_id",
    "doc_id",
    "page_no",
    "stage",
    "engine",
    "params_hash",
    "duration_ms",
    "cost",
    "verdict",
    "tier",
    "attribution",
    "action",
)


@dataclass(frozen=True, slots=True)
class Span:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    fields: Mapping[str, Any]


_current_span: ContextVar[Span | None] = ContextVar("dataagent_span", default=None)


def current_span() -> Span | None:
    return _current_span.get()


@contextmanager
def span(name: str, *, trace_id: str | None = None, **fields: Any) -> Iterator[Span]:
    """打开一个 span；子 span 继承 trace_id 并链接 parent_span_id。"""
    parent = _current_span.get()
    resolved_trace_id = trace_id or (parent.trace_id if parent else uuid.uuid4().hex)
    current = Span(
        trace_id=resolved_trace_id,
        span_id=uuid.uuid4().hex[:16],
        parent_span_id=None if parent is None else parent.span_id,
        name=name,
        fields=fields,
    )
    token = _current_span.set(current)
    try:
        yield current
    finally:
        _current_span.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        current = _current_span.get()
        if current is not None:
            payload["trace_id"] = current.trace_id
            payload["span_id"] = current.span_id
            payload["span"] = current.name
            if current.parent_span_id is not None:
                payload["parent_span_id"] = current.parent_span_id
            payload.update(current.fields)
        extra = getattr(record, "fields", None)
        if isinstance(extra, Mapping):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(*, level: int = logging.INFO, stream: Any = None) -> None:
    """把 dataagent logger 配成 JSON lines 输出（重复调用即重置 handler）。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """输出一条结构化事件；自动合并当前 span 上下文。"""
    logging.getLogger(LOGGER_NAME).log(level, event, extra={"fields": fields})
