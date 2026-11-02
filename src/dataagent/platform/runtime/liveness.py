"""worker 存活上报（外部状态，worker 进程不持有）。

worker 每个 poll 周期写一次 `dataagent:workers:<id>`（带 TTL）；status 读取最近
`alive_s` 内有心跳的 worker 数。进程被杀后 key 自然过期，不留下“假存活”。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import redis

DEFAULT_WORKER_PREFIX = "dataagent:workers:"
DEFAULT_WORKER_TTL_S = 120.0
DEFAULT_WORKER_ALIVE_S = 60.0


class WorkerLiveness(Protocol):
    def beat(self, worker_id: str, *, task_id: str | None = None) -> None: ...

    def alive(self) -> int: ...


@dataclass
class RedisWorkerRegistry:
    client: Any
    prefix: str = DEFAULT_WORKER_PREFIX
    ttl_s: float = DEFAULT_WORKER_TTL_S
    alive_s: float = DEFAULT_WORKER_ALIVE_S
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        if self.ttl_s <= 0 or self.alive_s <= 0:
            raise ValueError("ttl_s / alive_s 必须 > 0")

    def beat(self, worker_id: str, *, task_id: str | None = None) -> None:
        payload = json.dumps(
            {"ts": self.clock(), "task_id": task_id}, ensure_ascii=False
        )
        self.client.set(
            f"{self.prefix}{worker_id}", payload, ex=max(1, int(self.ttl_s))
        )

    def alive(self) -> int:
        now = self.clock()
        count = 0
        for key in self.client.scan_iter(match=f"{self.prefix}*"):
            raw = self.client.get(key)
            if raw is None:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                timestamp = float(json.loads(raw)["ts"])
            except (ValueError, KeyError, TypeError):
                continue
            if now - timestamp <= self.alive_s:
                count += 1
        return count


def worker_registry_from_url(
    url: str, **kwargs: Any
) -> RedisWorkerRegistry:
    return RedisWorkerRegistry(redis.Redis.from_url(url), **kwargs)


def worker_key(worker_id: str, prefix: str = DEFAULT_WORKER_PREFIX) -> str:
    return f"{prefix}{worker_id}"
