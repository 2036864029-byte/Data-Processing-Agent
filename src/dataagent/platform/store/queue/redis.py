"""Redis Streams 队列（最小语义，research D6）。

至少一次：XADD 入队、XREADGROUP 派发，消息未 ACK 前保留在待确认列表（PEL），
消费者崩溃后可被接管（XCLAIM，A6 续跑用）；手动确认：XACK 处理成功后调用；
死信：XACK + XDEL 移入死信流。全部封装，业务代码禁直接 redis.xadd（G9 门禁）。
不依赖延迟消息与优先级队列——应用层用 visible_at 实现。
"""

from __future__ import annotations

from typing import cast

import redis
import redis.exceptions
from redis.typing import EncodableT, FieldT

from dataagent.platform.store.queue.base import Queue, ReceivedMessage

# xreadgroup / xrange 的字段 dict：decode_responses=False 下键值均为 bytes
_Fields = dict[bytes, bytes]
_StreamItems = list[tuple[str, list[tuple[str, _Fields]]]]
_XAddFields = dict[FieldT, EncodableT]  # redis-py xadd 期望的字段类型


class RedisQueue(Queue):
    """基于 Redis Streams + Consumer Group 的最小队列实现。"""

    def __init__(
        self,
        client: redis.Redis,
        stream: str,
        group: str = "workers",
        consumer: str = "worker",
        dead_stream: str | None = None,
    ) -> None:
        self._r = client
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._dead = dead_stream or f"{stream}:dead"

    def enqueue(self, payload: bytes) -> str:
        return cast(str, self._r.xadd(self._stream, {"payload": payload}))

    def receive(self, max_count: int = 1) -> list[ReceivedMessage]:
        self._ensure_group()
        raw = cast(
            _StreamItems | None,
            self._r.xreadgroup(
                self._group, self._consumer, {self._stream: ">"}, count=max_count
            ),
        )
        messages: list[ReceivedMessage] = []
        if not raw:
            return messages
        for _, items in raw:
            for msg_id, fields in items:
                messages.append(ReceivedMessage(id=msg_id, payload=fields[b"payload"]))
        return messages

    def ack(self, message_id: str) -> None:
        self._r.xack(self._stream, self._group, message_id)

    def dead_letter(self, message_id: str) -> None:
        raw = self._r.xrange(self._stream, min=message_id, max=message_id)
        if raw:
            self._r.xadd(self._dead, cast(_XAddFields, raw[0][1]))
        self._r.xack(self._stream, self._group, message_id)
        self._r.xdel(self._stream, message_id)

    def pending_count(self) -> int:
        """待确认（PEL）消息数；消费组不存在时为 0。"""
        try:
            summary = self._r.xpending(self._stream, self._group)
        except redis.exceptions.ResponseError:
            return 0
        if not summary:
            return 0
        return int(summary["pending"])

    def dead_letter_count(self) -> int:
        return self._r.xlen(self._dead)

    def _ensure_group(self) -> None:
        try:
            self._r.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
