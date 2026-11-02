"""worker 存活上报测试：心跳写入、TTL 过期、存活计数。"""

from __future__ import annotations

import fnmatch
from typing import Any

from dataagent.platform.runtime.liveness import RedisWorkerRegistry, worker_key


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        if ex is not None:
            self.ttls[key] = ex

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def scan_iter(self, match: str) -> list[str]:
        return [key for key in self.data if fnmatch.fnmatch(key, match)]


def test_beat_marks_worker_alive_and_expires() -> None:
    now = [100.0]
    registry = RedisWorkerRegistry(FakeRedis(), clock=lambda: now[0])

    registry.beat("worker-1", task_id="task-1")

    assert registry.alive() == 1
    now[0] += 61
    assert registry.alive() == 0


def test_multiple_workers_and_key_prefix() -> None:
    client = FakeRedis()
    registry = RedisWorkerRegistry(client, clock=lambda: 0.0)

    registry.beat("a")
    registry.beat("b")

    assert worker_key("a") in client.data
    assert registry.alive() == 2


def test_invalid_payload_ignored() -> None:
    client = FakeRedis()
    client.data[f"{worker_key('bad')}"] = "not-json"
    registry = RedisWorkerRegistry(client, clock=lambda: 0.0)

    assert registry.alive() == 0


def test_invalid_ttl_rejected() -> None:
    try:
        RedisWorkerRegistry(FakeRedis(), ttl_s=0)
    except ValueError:
        return
    raise AssertionError("ttl_s=0 应被拒绝")


def test_fake_client_accepts_any_key() -> None:
    client: Any = FakeRedis()
    assert client.get("missing") is None
